# Copyright 2020-2022 Croix Bleue du Québec

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
from functools import cache
import re
import logging
import time
import inspect
import weakref

from copy import deepcopy
from attr import has
from fastapi import Request 
from contextlib import asynccontextmanager

from aiobitbucket.bitbucket import Bitbucket
from aiobitbucket.typing.refs import Branch
from aiobitbucket.apis.repositories.repository import RepoSlug
from aiobitbucket.errors import NetworkNotFound
from aiobitbucket.typing.repositories.commit_status import State as commit_status_state
from aiobitbucket.typing.webhooks.webhook import event_t as HookEvent_t
from ..realtime.hookserver import app_sccs
from ..plugin import Sccs
from ..errors import SccsException
from ..accesscontrol import AccessForbidden, Actions, Permissions
from ..utils import cd as utils_cd

from ..typing import cd as typing_cd
from ..typing import repositories as typing_repo

PLUGIN_NAME="bitbucketcloud"
def init_plugin():
    if BitbucketCloud._instance is None:
            BitbucketCloud._instance = BitbucketCloud()
    return PLUGIN_NAME , BitbucketCloud._instance

class BitbucketCloud(Sccs):
    hook_path = f"/{PLUGIN_NAME}/hooks/repo"
    _instance = None       

    async def init(self, core, args):
        """
        Initialize the plugin
        """

        self.cache_local_sessions={}
        self.lock_cache_local_sessions = asyncio.Lock()
        
        self.team = args["team"]

        self.cd_environments = args["continous_deployment"]["environments"]
        self.cd_branches_accepted = [env["branch"] for env in self.cd_environments]
        self.cd_pullrequest_tag = args["continous_deployment"]["pullrequest"]["tag"]
        self.cd_versions_available = args["continous_deployment"]["pipeline"]["versions_available"]
        
        if args['watcher'] is not None:
            self.watcher = Bitbucket()
            self.watcher.open_basic_session(args["watcher"]["user"], args["watcher"]["pwd"])
        
        self.accesscontrol_rules = {
            Actions.WATCH_CONTINOUS_DEPLOYMENT_CONFIG: Permissions.READ_CAPABILITIES,
            Actions.WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE: Permissions.READ_CAPABILITIES,
            Actions.WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE: Permissions.READ_CAPABILITIES
        }
        
        
        
        if hasattr(core, 'hookServer'):
            if not hasattr(self,"cache"):
                "create the nessesary caches"
                #self.cache = core.hookServer.create_dict()
                self.cache = {}
                self.cache["repo"]=core.hookServer.create_cache(self.get_repository,'repository',session=None)
                self.cache["continuousDeploymentConfig"]=core.hookServer.create_cache(self._fetch_continuous_deployment_config,'repository',session={'user':{'user':args["watcher"]["user"],'apikey':args["watcher"]["pwd"]}})
                self.cache["continuousDeploymentConfigAvailable"]=core.hookServer.create_cache(self._fetch_continuous_deployment_environments_available,'repository',session={'user':{'user':args["watcher"]["user"],'apikey':args["watcher"]["pwd"]}})
                self.cache["available"]=core.hookServer.create_cache(self._fetch_continuous_deployment_versions_available,'repository')
            self.__routing_init()
        BitbucketCloud.__instance = self

    def __routing_init(self): 
        """
        Initialise all the nessesary paths for hooks.
        """
        print("init routing")
        cust_logger = logging.getLogger("aiohttp.access") 
        
        @app_sccs.post(self.hook_path,)
        async def __handle_Hooks_Repo(request:Request):
            """
                handle the repo endpoint.
            """
            cust_logger.info("__handle_Hooks_Repo request")
            event = HookEvent_t(request.headers["X-Event-Key"])
            responseJson = await request.json()
            UUID = responseJson["repository"]["name"]
            
            if event == HookEvent_t.REPO_DELETED :
                cust_logger.info("__handle_delete_Repo")
                self.__handle_delete_repo(UUID)
            else:
                Workspace = responseJson["repository"]["workspace"]["slug"]
                
                self.cache["repo"][UUID] = RepoSlug(None,workspace_name=Workspace,repo_slug_name= responseJson["repository"]["name"],data=responseJson["repository"])
                if event == HookEvent_t.REPO_PUSH:
                    cust_logger.info("__handle_push_Repo")
                    await self.__handle_push(UUID,responseJson)

                elif event == HookEvent_t.REPO_COMMIT_STATUS_CREATED or event == HookEvent_t.REPO_COMMIT_STATUS_UPDATED :
                    cust_logger.info("__handle_commit_status")
                    await self.__handle_commit_status (UUID,event,responseJson)
                
        return __handle_Hooks_Repo

    def __handle_delete_repo(self,UUID):
        for key in self.cache:
                    if UUID in self.cache[key] :
                        del self.cache[key][UUID]

    async def __handle_push(self,UUID,responseJson):
        #only works with devops-console deployments
        triggerContinuousDeploymentPattern = re.compile(r'^deploy version ([0-9a-fA-F]+)$')
        
        for change in responseJson["push"]["changes"]:
            versionMatch = re.match(triggerContinuousDeploymentPattern,change["new"]["target"]["message"])
            if versionMatch:
                try:
                    newName = change["new"]["name"]
                    index = self.cd_branches_accepted.index(newName)
                    env = typing_cd.EnvironmentConfig(hash((UUID, newName)))
                    env.environment = self.cd_environments[index]["name"]
                    env.buildstatus = str(commit_status_state.INPROGRESS if (newName == "master") else commit_status_state.SUCCESSFUL )
                    env.version = versionMatch.group(1)
                    #import pdb; pdb.set_trace()
                    #! race condition here !
                    #with self.cache_continuousDeploymentConfig :
                        
                    tempDict = await self.cache["continuousDeploymentConfig"][UUID]
                    tempDict[newName]= env
                    self.cache["continuousDeploymentConfig"][UUID] = tempDict

                except ValueError:
                    pass
        
    async def __handle_commit_status(self,UUID,event,response_json):
        """
        This hook is only called on the Master branch for handling compilation events  
        """
        cust_logger = logging.getLogger("aiohttp.access") 
        cust_logger.info("handle commit status fct")
        cust_logger.info(f"info: {response_json['commit_status']}")
        # cust_logger.info(f"list branch accepted : {self.cd_branches_accepted}")
        
        repoName=response_json['commit_status']['repository']['name']
        refName=response_json["commit_status"]["refname"]
        
        if(refName in self.cd_branches_accepted):
            # cust_logger.info("refname in version available")
            
            curr_status_state = response_json["commit_status"]["state"]

            #get the build number
            build_nb = re.search("/(\d+)$",response_json["commit_status"]["url"]).group(1)
            cust_logger.info(f"build_nb : {build_nb}")
            
            env = self.cd_environments[self.cd_branches_accepted.index(refName)]
            if(event ==  HookEvent_t.REPO_COMMIT_STATUS_CREATED):
                cust_logger.info(f"commit CREATED : {UUID}")
                tmp = self._create_continuous_deployment_config_by_branch(repoName,build_nb,refName,env)
                cust_logger.info(f"create config by branch : {tmp}")
                
                cacheConfig = await self.cache["continuousDeploymentConfig"][UUID]
                cust_logger.info(f"cache config for {UUID} is : {cacheConfig}")
                cacheConfig[refName] = tmp
                self.cache["continuousDeploymentConfig"][UUID] = cacheConfig

            # todo : use commit_status_state.SUCCESSFUL
            if(curr_status_state == "SUCCESSFUL"):
                #add it to the available cache
                cust_logger.info(f"commit SUCCESSFUL : {UUID}")
                
                # result=[]
                # i = 0
                # async for conf in self.cache["available"][UUID]:
                #     tmpbuild=str(conf.build)
                #     cust_logger.info(f"async conf build is : {tmpbuild}")
                #     result.append(conf)
                    
                #     if conf.build > int(build_nb) :
                #         i+=1
                #     elif conf.build == int(build_nb):
                #         break
                #     else:
                #         version = response_json["commit_status"]["commit"]["hash"]
                
                #         available = typing_cd.Available(hash((UUID,build_nb)))
                #         available.build = build_nb
                #         available.version = version

                #         result.insert(i,available)
                #         break
                
                # cust_logger.info(f"async self cache set uuid : {UUID} with value {result}")
                
                local_available = await self.cache["available"][UUID]

                # cust_logger.info(f"local available : {local_available}")
                i = 0
                for conf in local_available:
                    tmpbuild=str(conf.build)
                    cust_logger.info(f"conf build is : {tmpbuild}")
                    if conf.build > int(build_nb) :
                        i+=1
                    elif conf.build == int(build_nb):
                        break
                    else:
                        version = response_json["commit_status"]["commit"]["hash"]
                
                        available = typing_cd.Available(hash((UUID,build_nb)))
                        available.build = int(build_nb)
                        available.version = version

                        local_available.insert(i,available)
                        break
                        
                cust_logger.info(f"self cache set uuid : {UUID} with value {local_available}")
                self.cache["available"][UUID] = local_available
        else:
            cust_logger.info(f"not in cd_version_available : {self.cd_versions_available}")

    async def cleanup(self):
        if hasattr(self, 'watcher'):
            await self.watcher.close_session()
        if hasattr(self,'cache'):
            self.reset_cache()
    
    def reset_cache(self):
        for key in self.cache :
            self.cache[key].clear_cache()
            
    def get_session_id(self,args):
        """see plugin.py"""

        session_id = hash((args["user"], args["apikey"]))

        logging.debug(f"get session id: {session_id}")
        return session_id

    async def open_session(self, session_id, args):
        """see plugin.py"""

        async with self.lock_cache_local_sessions:
            existing_session = self.cache_local_sessions.get(session_id)

            if existing_session is not None:
                existing_session["shared-session"] += 1
                logging.debug(f'reuse session {session_id} (shared: {existing_session["shared-session"]})')
                return existing_session

            logging.debug(f'create a new session {session_id}')
            new_session = {
                "session_id": session_id,
                "shared-session": 1,
                "user": {
                    "user": args["user"],
                    "apikey": args["apikey"],
                    "team": self.team,
                    "author": args["author"]
                },
                "cache": {
                    "repositories": {
                        "values": [],
                        "last_access": 0,
                        "ttl": 7200
                    }
                }
            }

            self.cache_local_sessions[session_id] = new_session

            return new_session

    def get_session(self, session_id) -> dict:
        return self.cache_local_sessions.get(session_id)

    async def close_session(self, session_id, session, args):
        """see plugin.py"""
        
        async with self.lock_cache_local_sessions:
            session["shared-session"] -= 1

            logging.debug(f'close session {session_id} (shared: {session["shared-session"]})')

            if session["shared-session"] <= 0:
                # not used anymore
                logging.debug(f"remove session {session_id} from cache")
                self.cache_local_sessions.pop(session_id)

    @asynccontextmanager
    async def bitbucket_session(self, session, default_session=None):
        
        if isinstance(session,type(Bitbucket)):
            yield session 
            return
        
        # Use default session if session is not provided (mainly used for watch requests with prior accesscontrol calls)
        if session is None:
            yield default_session
            return
        # Regular flow
        bitbucket = Bitbucket()
        try:
            bitbucket.open_basic_session(
                session["user"]["user"],
                session["user"]["apikey"]
            )
            yield bitbucket
        finally:
            await bitbucket.close_session()

    async def accesscontrol(self, session, repository, action, args):
        """see plugin.py"""
        logging.debug(f"access control for {repository}")

        using_cache = (time.time() - session["cache"]["repositories"]["last_access"]) < session["cache"]["repositories"]["ttl"]
        repo = None

        if using_cache:
            logging.debug("access control: using cache")
            # TODO: Optimize
            for value in session["cache"]["repositories"]["values"]:
                if value.name == repository:
                    repo = value
                    break
        else:
            logging.debug("access control: cache is invalid; direct API calls")
            async with self.bitbucket_session(session) as bitbucket:
                repo = await bitbucket.user.permissions.repositories.get_by_full_name(self.team + "/" + repository)
                # no need to convert to typing_repo.Repository() as both expose permission attributes in the same way

        if repo is None:
            # No read/write or admin access on this repository
            raise AccessForbidden(repository, action)

        if repo.permission not in self.accesscontrol_rules.get(action, []):
            raise AccessForbidden(repository, action)

    async def get_repositories(self, session, args) -> list:
        """see plugin.py"""

        result = []
        async with self.bitbucket_session(session) as bitbucket:
            async for permission_repo in bitbucket.user.permissions.repositories.get():
                repo = typing_repo.Repository(hash(permission_repo.repository.name))
                repo.name = permission_repo.repository.name
                repo.permission = permission_repo.permission
                result.append(repo)

        # caching repositories for internal usage
        async with self.lock_cache_local_sessions:
            session["cache"]["repositories"]["values"] = result
            session["cache"]["repositories"]["last_access"] = time.time()

        return result

    async def get_repository(self, session, repository, args) -> list:
        """see plugin.py"""
        self.__log_session(session)
        async with self.bitbucket_session(session) as bitbucket:
            permission = await bitbucket.user.permissions.repositories.get_by_full_name(self.team + "/" + repository)
            repo = typing_repo.Repository(hash(permission.repository.name))
            repo.name = permission.repository.name
            repo.permission = permission.permission

            return repo

    def _create_continuous_deployment_config_by_branch(self, repository: str,version: str,branch: str, config: dict,pullrequest:str=None,buildStatus:str = "SUCCESSFUL")->typing_cd.EnvironmentConfig:
        """
        Helper function to standarise the creation of EnvironementConfig
        """
        env = typing_cd.EnvironmentConfig(hash((repository, branch)))
        env.version = version
        env.environment = config["name"]
        trigger_config = config.get("trigger", {})
        env.readonly = not trigger_config.get("enabled", True)
        if trigger_config.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            env.pullrequest = pullrequest
        return env

    async def _get_continuous_deployment_config_by_branch(self, repository: str, repo: RepoSlug, branch: Branch, config: dict) ->  tuple[str,typing_cd.EnvironmentConfig]:
        """
        Get environment configuration for a specific branch
        """
        logging.debug(f"_get_continuous_deployment_config_by_branch for {repository} on {branch.name}")

        # Get version
        file_version = config["version"].get("file")
        if file_version is not None:
            version = (await repo.src().download(branch.target.hash, file_version)).strip()
        elif config["version"].get("git") is not None:
            version = branch.target.hash
        else:
            raise NotImplementedError()

        trigger_config = config.get("trigger", {})
        pullrequest_link = None
        if trigger_config.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            async for pullrequest in repo.pullrequests().get():
                if pullrequest.destination.branch.name == config["branch"] and self.cd_pullrequest_tag in pullrequest.title:
                    pullrequest_link = pullrequest.links.html.href
                    break

        return (branch.name,self._create_continuous_deployment_config_by_branch(repository,version,branch.name,config,pullrequest_link))

    async def _fetch_continuous_deployment_config(self, repository,session=None,environments=None)->dict:
        """
        fetch the continous deployment config from the bitbucket servers
        """
        self.__log_session(session)
        deploys = []
        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            repo = bitbucket.repositories.repo_slug(self.team, repository)
            
            # Get supported branches
            async for branch in repo.refs().branches.get():
                try:
                    index = self.cd_branches_accepted.index(branch.name)
                    if environments is None or self.cd_environments[index]["name"] in environments:
                        deploys.append((branch, index))
                except ValueError:
                    pass

            # Do we have something to do ?
            if len(deploys) == 0:
                raise SccsException("continuous deployment seems not supported for {}".format(repository))

            # Ordered deploys
            deploys = sorted(deploys, key=lambda deploy: deploy[1])

            # Get continuous deployment config for all environments selected
            tasks = []
            for branch, index in deploys:
                tasks.append(
                    self._get_continuous_deployment_config_by_branch(
                        repository,
                        repo,
                        branch,
                        self.cd_environments[index])
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

        response = {}
        for result in results:
            #response.append(result[1])
            response[result[0]]=result[1]
        
        return response

    async def get_continuous_deployment_config(self, session, repository, environments=None, args=None):
        """
        See plugin.py
        """
        #return await  self._fetch_continuous_deployment_config(repository,session,environments)
        results = []
        #Fetch in the cache
        TempDict = await self.cache["continuousDeploymentConfig"][repository]
        if environments is not None :
            for branch in TempDict:
                # import pdb;pdb.set_trace()
                if TempDict[branch].environment in environments:
                    results.append(TempDict[branch])
        else:
            results = [TempDict[branch] for branch in TempDict]
        return results  

    async def _fetch_continuous_deployment_environments_available(self, repository,session=None) -> list:
        """
        fetch the available environements for the specified repository.
        """
        self.__log_session(session)
        cust_logger = logging.getLogger("aiohttp.access") 
        cust_logger.info("_fetch_continuous_deployment_environments_available")
        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            repo = bitbucket.repositories.repo_slug(self.team, repository)

            availables = []

            # Get supported branches
            async for branch in repo.refs().branches.get():
                try:
                    index = self.cd_branches_accepted.index(branch.name)
                    env = typing_cd.EnvironmentConfig(hash((repository, branch.name)))
                    env.environment = self.cd_environments[index]["name"]
                    availables.append((env, index))
                except ValueError:
                    pass

            # Ordered availables and remove index
            response = [env for env, _ in sorted(availables, key=lambda available: available[1])]
            cust_logger.info("_fetch_continuous_deployment_environments_available : {response}")
            return response

    async def get_continuous_deployment_environments_available(self, session, repository, args) -> list:
            #return await self._fetch_continuous_deployment_environments_available(repository,session)
            return await self.cache["continuousDeploymentConfigAvailable"][repository]

    async def _fetch_continuous_deployment_versions_available(self, repository, session=None) -> list:
        
        cust_logger = logging.getLogger("aiohttp.access") 
        cust_logger.info(f"_fetch_continuous_deployment_versions_available on repo : {repository}")
        
        self.__log_session(session)
        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            # commits available to be deployed
            repo = bitbucket.repositories.repo_slug(self.team, repository)

            response = []

            cust_logger.info(f"list cd version available : {self.cd_versions_available}")
            
            async for pipeline in repo.pipelines().get(filter='sort=-created_on'):
                if pipeline.target.ref_name in self.cd_versions_available and \
                   pipeline.state.result.name == "SUCCESSFUL":
                    # cust_logger.info(f"pipeline available : {pipeline}")
                    available = typing_cd.Available(hash((repository, pipeline.build_number)))
                    available.build = pipeline.build_number
                    available.version = pipeline.target.commit.hash
                    
                    
                    response.append(available)

                # else:
                    # cust_logger.info(f"pipeline not available : {pipeline}")
                    
            cust_logger.info(f"version on repo : {repository} is : {response}")
            return response

    async def get_continuous_deployment_versions_available(self, session, repository, args) -> list:
        #if(session is not None):
            #return await self._fetch_continuous_deployment_versions_available(repository,session)
        return await self.cache["available"][repository]

    async def trigger_continuous_deployment(self, session, repository, environment, version, args) -> typing_cd.EnvironmentConfig:
        """see plugin.py"""
        
        logging.debug(f"trigger for {repository} on {environment}")

        #logging.debug(f"session = {session}" )

        # Get Continuous Deployment configuration for the environment requested
        cd_environment_config = None
        for cd_environment in self.cd_environments:
            if cd_environment["name"] == environment:
                cd_environment_config = cd_environment
                break
        if cd_environment_config is None:
            utils_cd.trigger_not_supported(repository, environment)
        
        #logging.debug(f"cd_environment_config = {cd_environment_config}")
        
        #using user session for repo manipulations
        async with self.bitbucket_session(session) as bitbucket:
            # Check current configuration using the cache. This is ok because the user will see that deployed version anyway
            continuous_deployment = (await self.get_continuous_deployment_config(None, repository, environments=[environment]))[0]
            #
            versions_available = await self.get_continuous_deployment_versions_available(None, repository, args)
            utils_cd.trigger_prepare(continuous_deployment, versions_available, repository, environment, version)

            # Check if we need/can do a PR
            repo = bitbucket.repositories.repo_slug(self.team, repository)
            branch = cd_environment_config["branch"]

            if cd_environment_config.get("trigger", {}).get("pullrequest", False):
                # Continuous Deployment is done with a PR.
                # We need to check if there is already one open (the version requested doesn't matter)
                async for pullrequest in repo.pullrequests().get():
                    if pullrequest.destination.branch.name == branch and self.cd_pullrequest_tag in pullrequest.title:
                        raise SccsException(f"A continuous deployment request is already open. link: {pullrequest.links.html.href}")

                deploy_branch = repo.refs().branches.by_name(branch)
                await deploy_branch.get()
                deploy_branch.name = f"continuous-deployment-{environment}"
                try:
                    #If the branch already exist , we should remove it.
                    await  deploy_branch.delete()
                except NetworkNotFound :
                    pass
                await deploy_branch.create()
            else:
                deploy_branch = None

            # Upgrade/Downgrade request
            await repo.src().upload_pure_text(
                cd_environment_config["version"]["file"],
                f"{version}\n",
                f"deploy version {version}",
                session["user"]["author"],
                branch if deploy_branch is None else deploy_branch.name
            )
            
            with self.cache["continuousDeploymentConfig"] as cache :
                if deploy_branch is not None:
                    # Continuous Deployment is done with a PR.
                    pr = repo.pullrequests().new()
                    pr.title = f"Ugrade {environment} {self.cd_pullrequest_tag}"
                    pr.close_source_branch = True
                    pr.source.branch.name = deploy_branch.name
                    pr.destination.branch.name = branch
                    
                    #race condition start here 
                    await pr.create()
                    await pr.get()
                    continuous_deployment.pullrequest = pr.links.html.href
                else:
                    # Continuous Deployment done
                    continuous_deployment.version = version

                #race condition finish after that statement.
                cache[deploy_branch] = continuous_deployment
            #(await self.cache["continuousDeploymentConfig"][repository])[deploy_branch] = continuous_deployment

            # Return the new configuration (new version or PR in progress)
            return continuous_deployment
        
    async def get_hooks_repository(self,session,repository,args):
        """see plugin.py"""
        async with self.bitbucket_session(session) as bitbucket:
            permission = await bitbucket.webhooks.get_by_repository_name(self.team + "/" + repository)
            repo = typing_repo.Repository(hash(permission.repository.name))
            repo.name = permission.repository.name
            repo.permission = permission.permission

            return repo
    
    def __log_session(self,session:dict):
        """
        helper function for keeping track of who calls what.
        """
        cust_logger = logging.getLogger("aiohttp.access") 
        funcName = inspect.getouterframes(inspect.currentframe(), 2)[1][3] #gets the function name using the callstack
        username =  "Watcher" 
        if session is not None:#by default  None is the watcher
            username = session['user']['user']
        cust_logger.debug(f"{username} called {funcName}")
    
    def __new__(cls):
        logging.debug("new bitbucket")
        return super().__new__(cls)
    
    def __del__(self):
        app_sccs.delete(self.hook_path)