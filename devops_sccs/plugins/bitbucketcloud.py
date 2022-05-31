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
from __future__ import annotations
import asyncio
import logging
import time
import inspect
from typing import Dict

from fastapi import Request
from contextlib import asynccontextmanager

from aiobitbucket.bitbucket import Bitbucket
from aiobitbucket.typing.refs import Branch
from aiobitbucket.apis.repositories.repository import RepoSlug
from aiobitbucket.errors import NetworkNotFound
from aiobitbucket.typing.webhooks.webhook import event_t as HookEvent_t
from ..realtime.hookserver import app_sccs
from ..plugin import Sccs
from ..errors import SccsException
from ..accesscontrol import AccessForbidden, Action, Permission
from ..utils import cd as utils_cd
from ..typing import cd as typing_cd
from ..typing import repositories as typing_repo

PLUGIN_NAME = "bitbucketcloud"


def init_plugin():
    if BitbucketCloud._instance is None:
        BitbucketCloud._instance = BitbucketCloud()
    return PLUGIN_NAME, BitbucketCloud._instance


class BitbucketCloud(Sccs):
    hook_path = f"/{PLUGIN_NAME}/hooks/repo"
    _instance: BitbucketCloud | None = None

    async def init(self, core, args):
        """
        Initialize the plugin
        """

        self.cache_local_sessions = {}
        self.lock_cache_local_sessions = asyncio.Lock()

        self.team = args["team"]

        self.cd_environments = args["continuous_deployment"]["environments"]
        self.cd_branches_accepted = [env["branch"] for env in self.cd_environments]
        self.cd_pullrequest_tag = args["continuous_deployment"]["pullrequest"]["tag"]
        self.cd_versions_available = args["continuous_deployment"]["pipeline"][
            "versions_available"
        ]

        self.watcherUser = args["watcher"]["user"]
        self.watcherPwd = args["watcher"]["pwd"]

        if args["watcher"] is not None:
            self.watcher = Bitbucket()
            self.watcher.open_basic_session(self.watcherUser, self.watcherPwd)

        self.accesscontrol_rules: Dict[int, list[str]] = {
            Action.WATCH_CONTINOUS_DEPLOYMENT_CONFIG: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE: Permission.READ_CAPABILITIES,
        }

        # if hasattr(core, "hookServer"):
        #     if not hasattr(self, "cache"):
        #         "create the nessesary caches"
        #         # self.cache = core.hookServer.create_dict()
        #         self.cache = {}
        #         self.cache["repo"] = core.hookServer.create_cache(
        #             self.get_repository, "repository", session=None
        #         )
        #         self.cache["continuousDeploymentConfig"] = core.hookServer.create_cache(
        #             self._fetch_continuous_deployment_config,
        #             "repository",
        #             session={
        #                 "user": {
        #                     "user": args["watcher"]["user"],
        #                     "apikey": args["watcher"]["pwd"],
        #                 }
        #             },
        #         )
        #         self.cache[
        #             "continuousDeploymentConfigAvailable"
        #         ] = core.hookServer.create_cache(
        #             self._fetch_continuous_deployment_environments_available,
        #             "repository",
        #             session={
        #                 "user": {
        #                     "user": args["watcher"]["user"],
        #                     "apikey": args["watcher"]["pwd"],
        #                 }
        #             },
        #         )
        #         self.cache["available"] = core.hookServer.create_cache(
        #             self._fetch_continuous_deployment_versions_available, "repository"
        #         )
        #     self.__routing_init()
        BitbucketCloud.__instance = self

    def __routing_init(self):
        """
        Initialise all the nessesary paths for hooks.
        """

        @app_sccs.post(
            self.hook_path,
        )
        async def __handle_Hooks_Repo(request: Request):
            """
            handle the repo endpoint.
            """
            logging.info("__handle_Hooks_Repo request")
            event = HookEvent_t(request.headers["X-Event-Key"])
            responseJson = await request.json()

            repoName = responseJson["repository"]["name"]

            if event == HookEvent_t.REPO_DELETED:
                logging.info("__handle_delete_Repo")
                self.__handle_delete_repo(repoName)
            else:
                Workspace = responseJson["repository"]["workspace"]["slug"]

                self.cache["repo"][repoName] = RepoSlug(
                    None,
                    workspace_name=Workspace,
                    repo_slug_name=responseJson["repository"]["name"],
                    data=responseJson["repository"],
                )
                if event == HookEvent_t.REPO_PUSH:
                    logging.info("skip __handle_push_Repo")
                    await self.__handle_push(repoName, responseJson)

                elif (
                    event == HookEvent_t.REPO_COMMIT_STATUS_CREATED
                    or event == HookEvent_t.REPO_COMMIT_STATUS_UPDATED
                ):
                    logging.info("__handle_commit_status")
                    await self.__handle_commit_status(repoName, event, responseJson)

        return __handle_Hooks_Repo

    def __handle_delete_repo(self, repoName):
        for key in self.cache:
            if repoName in self.cache[key]:
                del self.cache[key][repoName]

    async def __handle_push(self, repoName, responseJson):
        """
        This hook is called on a branch commit
        """
        logging.info("handle push status fct")

        changesMatter = False
        for change in responseJson["push"]["changes"]:
            branchName = change["new"]["name"]
            if branchName in self.cd_branches_accepted:
                changesMatter = True
                break

        if not changesMatter:
            logging.debug(
                f"branch not in environment accepted : {self.cd_versions_available}"
            )
            return

        self.hookWatcher = Bitbucket()
        self.hookWatcher.open_basic_session(self.watcherUser, self.watcherPwd)
        newVersionDeployed = await self._fetch_bitbucket_continuous_deployment_config(
            repoName, self.hookWatcher
        )
        await self.hookWatcher.close_session()

        logging.info(f"handle push detected new version : {newVersionDeployed}")
        self.cache["continuousDeploymentConfig"][repoName] = newVersionDeployed

    async def __handle_commit_status(self, repoName, event, responseJson):
        """
        This hook is called on pipeline status create or update
        """
        logging.info("handle commit status fct")
        branchName = responseJson["commit_status"]["refname"]

        if branchName not in self.cd_branches_accepted:
            logging.debug(
                f"branch name : {branchName} not in environment accepted : {self.cd_versions_available}"
            )
            return

        if event == HookEvent_t.REPO_COMMIT_STATUS_CREATED:
            logging.debug(f"REPO_COMMIT_STATUS_CREATED : {repoName}")

            self.hookWatcher = Bitbucket()
            self.hookWatcher.open_basic_session(self.watcherUser, self.watcherPwd)
            newVersionDeployed = (
                await self._fetch_bitbucket_continuous_deployment_config(
                    repoName, self.hookWatcher
                )
            )
            await self.hookWatcher.close_session()

            logging.debug(
                f"REPO_COMMIT_STATUS_CREATED, new deployment config : {newVersionDeployed}"
            )
            self.cache["continuousDeploymentConfig"][repoName] = newVersionDeployed

        elif event == HookEvent_t.REPO_COMMIT_STATUS_UPDATED:
            logging.debug(f"REPO_COMMIT_STATUS_UPDATED : {repoName}")

            self.hookWatcher = Bitbucket()
            self.hookWatcher.open_basic_session(self.watcherUser, self.watcherPwd)
            version_available = (
                await self._fetch_bitbucket_continuous_deployment_versions_available(
                    repoName, self.hookWatcher
                )
            )
            await self.hookWatcher.close_session()

            logging.debug(
                f"REPO_COMMIT_STATUS_UPDATED, new version available : {version_available}"
            )
            self.cache["available"][repoName] = version_available

    async def cleanup(self):
        if hasattr(self, "watcher"):
            await self.watcher.close_session()
        if hasattr(self, "cache"):
            self.reset_cache()

    def reset_cache(self):
        for key in self.cache:
            self.cache[key].clear_cache()

    def get_session_id(self, args):
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
                logging.debug(
                    f'reuse session {session_id} (shared: {existing_session["shared-session"]})'
                )
                return existing_session

            logging.debug(f"create a new session {session_id}")
            new_session = {
                "session_id": session_id,
                "shared-session": 1,
                "user": {
                    "user": args["user"],
                    "apikey": args["apikey"],
                    "team": self.team,
                    "author": args["author"],
                },
                "cache": {
                    "repositories": {"values": [], "last_access": 0, "ttl": 7200}
                },
            }

            self.cache_local_sessions[session_id] = new_session

            return new_session

    def get_session(self, session_id) -> dict:
        return self.cache_local_sessions.get(session_id)

    async def close_session(self, session_id, session, args):
        """see plugin.py"""

        async with self.lock_cache_local_sessions:
            session["shared-session"] -= 1

            logging.debug(
                f'close session {session_id} (shared: {session["shared-session"]})'
            )

            if session["shared-session"] <= 0:
                # not used anymore
                logging.debug(f"remove session {session_id} from cache")
                self.cache_local_sessions.pop(session_id)

    @asynccontextmanager
    async def bitbucket_session(self, session, default_session=None):

        if isinstance(session, type(Bitbucket)):
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
                session["user"]["user"], session["user"]["apikey"]
            )
            yield bitbucket
        finally:
            await bitbucket.close_session()

    async def accesscontrol(self, session, repository, action, args):
        """see plugin.py"""
        logging.debug(f"access control for {repository}")

        using_cache = (
            time.time() - session["cache"]["repositories"]["last_access"]
        ) < session["cache"]["repositories"]["ttl"]
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
                repo = await bitbucket.user.permissions.repositories.get_by_full_name(
                    self.team + "/" + repository
                )
                # no need to convert to typing_repo.Repository() as both expose permission attributes in the same way

        if repo is None:
            # No read/write or admin access on this repository
            raise AccessForbidden(repository, action)

        if repo.permission not in self.accesscontrol_rules.get(action, []):
            raise AccessForbidden(repository, action)

    async def get_repositories(self, session, args) -> list:
        """see plugin.py"""
        logging.debug(f"get user permission repositories")
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
            permission = await bitbucket.user.permissions.repositories.get_by_full_name(
                self.team + "/" + repository
            )
            repo = typing_repo.Repository(hash(permission.repository.name))
            repo.name = permission.repository.name
            repo.permission = permission.permission

            return repo

    def _create_continuous_deployment_config_by_branch(
        self,
        repository: str,
        version: str,
        branch: str,
        config: dict,
        pullrequest: str = None,
        buildStatus: str = "SUCCESSFUL",
    ) -> typing_cd.EnvironmentConfig:
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

    async def _get_continuous_deployment_config_by_branch(
        self, repository: str, repo: RepoSlug, branch: Branch, config: dict
    ) -> tuple[str, typing_cd.EnvironmentConfig]:
        """
        Get environment configuration for a specific branch
        """
        logging.debug(
            f"_get_continuous_deployment_config_by_branch for {repository} on {branch.name}"
        )

        # Get version
        file_version = config["version"].get("file")
        if file_version is not None:
            version = (
                await repo.src().download(branch.target.hash, file_version)
            ).strip()
        elif config["version"].get("git") is not None:
            version = branch.target.hash
        else:
            raise NotImplementedError()

        trigger_config = config.get("trigger", {})
        pullrequest_link = None
        if trigger_config.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            async for pullrequest in repo.pullrequests().get():
                if (
                    pullrequest.destination.branch.name == config["branch"]
                    and self.cd_pullrequest_tag in pullrequest.title
                ):
                    pullrequest_link = pullrequest.links.html.href
                    break

        return (
            branch.name,
            self._create_continuous_deployment_config_by_branch(
                repository, version, branch.name, config, pullrequest_link
            ),
        )

    async def _fetch_continuous_deployment_config(
        self, repository, session=None, environments=None
    ) -> dict:
        """
        fetch the version deployed in each environment
        """
        self.__log_session(session)
        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            response = await self._fetch_bitbucket_continuous_deployment_config(
                repository, bitbucket, environments
            )

        return response

    async def _fetch_bitbucket_continuous_deployment_config(
        self, repository, bitbucket, environments=None
    ) -> dict:
        """
        fetch from bitbucket the version deployed in each environment
        """
        deploys = []
        repo = bitbucket.repositories.repo_slug(self.team, repository)

        # Get supported branches
        async for branch in repo.refs().branches.get():
            try:
                index = self.cd_branches_accepted.index(branch.name)
                if (
                    environments is None
                    or self.cd_environments[index]["name"] in environments
                ):
                    deploys.append((branch, index))
            except ValueError:
                pass

        # Do we have something to do ?
        if len(deploys) == 0:
            raise SccsException(
                "continuous deployment seems not supported for {}".format(repository)
            )

        # Ordered deploys
        deploys = sorted(deploys, key=lambda deploy: deploy[1])

        # Get continuous deployment config for all environments selected
        tasks = []
        for branch, index in deploys:
            tasks.append(
                self._get_continuous_deployment_config_by_branch(
                    repository, repo, branch, self.cd_environments[index]
                )
            )

        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        response = {}
        for [branch, results] in task_results:
            response[branch] = results

        results = [response[branch] for branch in response]
        logging.debug(
            f"_fetch_continuous_deployment_config for {repository} result is : {results}"
        )
        return results

    async def get_continuous_deployment_config(
        self, session, repository, environments=None, args=None
    ):
        """
        Get the deployed environment with the current version/tag
        """
        results = await self.cache["continuousDeploymentConfig"][repository]
        logging.debug(f"get_continuous_deployment_config : {results}")
        return results

    async def _fetch_continuous_deployment_environments_available(
        self, repository, session=None
    ) -> list:
        """
        fetch the available environements for the specified repository.
        """
        self.__log_session(session)
        logging.debug(
            f"_fetch_continuous_deployment_environments_available on repo : {repository}"
        )

        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            response = await self._fetch_bitbucket_continuous_deployment_environments_available(
                repository, bitbucket
            )

        return response

    async def _fetch_bitbucket_continuous_deployment_environments_available(
        self, repository, bitbucket
    ) -> list:

        logging.info(
            f"_fetch_bitbucket_continuous_deployment_environments_available --- team : {self.team} --- repo : {repository}"
        )
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
        response = [
            env for env, _ in sorted(availables, key=lambda available: available[1])
        ]
        logging.debug(
            f"_fetch_continuous_deployment_environments_available : {response}"
        )
        return response

    async def get_continuous_deployment_environments_available(
        self, session, repository, args
    ) -> list:
        result = await self.cache["continuousDeploymentConfigAvailable"][repository]
        logging.debug(
            f"get_continuous_deployment_environments_available on repo : {repository} --- result : {result}"
        )
        return result

    async def _fetch_continuous_deployment_versions_available(
        self, repository, session=None
    ) -> list:
        """
        Get the list of version available to deploy
        """
        logging.info(
            f"_fetch_continuous_deployment_versions_available on repo : {repository}"
        )

        self.__log_session(session)
        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            response = (
                await self._fetch_bitbucket_continuous_deployment_versions_available(
                    repository, bitbucket
                )
            )

        logging.info(
            f"_fetch_continuous_deployment_versions_available on repo : {repository} result : {response}"
        )
        return response

    async def _fetch_bitbucket_continuous_deployment_versions_available(
        self, repository, bitbucket
    ) -> list:

        repo = bitbucket.repositories.repo_slug(self.team, repository)

        response = []
        logging.info(f"list cd version available : {self.cd_versions_available}")

        async for pipeline in repo.pipelines().get(
            filter="q=target.ref_name=master&sort=-created_on"
        ):
            if (
                pipeline.target.ref_name in self.cd_versions_available
                and pipeline.state.result.name == "SUCCESSFUL"
            ):
                available = typing_cd.Available(
                    hash((repository, pipeline.build_number))
                )
                available.build = pipeline.build_number
                available.version = pipeline.target.commit.hash
                logging.debug(
                    f"adding version available for build nb : {pipeline.build_number}"
                )
                response.append(available)

        return response

    async def get_continuous_deployment_versions_available(
        self, session, repository, args
    ) -> list:
        result = await self.cache["available"][repository]
        logging.debug(
            f"get_continuous_deployment_versions_available for repo : {repository}"
        )
        return result

    async def trigger_continuous_deployment(
        self, session, repository, environment, version, args
    ) -> typing_cd.EnvironmentConfig:
        """
        Trigger a deployment in a specific environment
        """
        logging.info(f"Trigger deploy for {repository} on {environment}")

        # Get Continuous Deployment configuration for the environment requested
        cd_environment_config = None
        for cd_environment in self.cd_environments:
            if cd_environment["name"] == environment:
                cd_environment_config = cd_environment
                break
        if cd_environment_config is None:
            utils_cd.trigger_not_supported(repository, environment)

        # using user session for repo manipulations
        async with self.bitbucket_session(session) as bitbucket:
            # Check current configuration using the cache. This is ok because the user will see that deployed version anyway
            list_continuous_deployment = await self.get_continuous_deployment_config(
                None, repository, environments=[environment]
            )
            continuous_deployment = list_continuous_deployment[0]

            for env in list_continuous_deployment:
                if env.environment == environment:
                    continuous_deployment = env

            logging.debug(
                f"Trigger deploy on env : {environment} with on env : {continuous_deployment}"
            )

            versions_available = (
                await self.get_continuous_deployment_versions_available(
                    None, repository, args
                )
            )

            utils_cd.trigger_prepare(
                continuous_deployment,
                versions_available,
                repository,
                environment,
                version,
            )

            # Check if we need/can do a PR
            repo = bitbucket.repositories.repo_slug(self.team, repository)
            branch = cd_environment_config["branch"]

            if cd_environment_config.get("trigger", {}).get("pullrequest", False):
                # Continuous Deployment is done with a PR.
                # We need to check if there is already one open (the version requested doesn't matter)
                async for pullrequest in repo.pullrequests().get():
                    if (
                        pullrequest.destination.branch.name == branch
                        and self.cd_pullrequest_tag in pullrequest.title
                    ):
                        raise SccsException(
                            f"A continuous deployment request is already open. link: {pullrequest.links.html.href}"
                        )

                deploy_branch = repo.refs().branches.by_name(branch)
                await deploy_branch.get()
                deploy_branch.name = f"continuous-deployment-{environment}"
                try:
                    # If the branch already exist , we should remove it.
                    await deploy_branch.delete()
                except NetworkNotFound:
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
                branch if deploy_branch is None else deploy_branch.name,
            )

            with self.cache["continuousDeploymentConfig"] as cache:
                if deploy_branch is not None:
                    # Continuous Deployment is done with a PR.
                    pr = repo.pullrequests().new()
                    pr.title = f"Ugrade {environment} {self.cd_pullrequest_tag}"
                    pr.close_source_branch = True
                    pr.source.branch.name = deploy_branch.name
                    pr.destination.branch.name = branch

                    # race condition start here
                    await pr.create()
                    await pr.get()
                    continuous_deployment.pullrequest = pr.links.html.href
                else:
                    # Continuous Deployment done
                    continuous_deployment.version = version

                # race condition finish after that statement.
                cache[deploy_branch] = continuous_deployment

            # Return the new configuration (new version or PR in progress)
            return continuous_deployment

    async def get_hooks_repository(self, session, repository, args):
        """see plugin.py"""
        async with self.bitbucket_session(session) as bitbucket:
            permission = await bitbucket.webhooks.get_by_repository_name(
                self.team + "/" + repository
            )
            repo = typing_repo.Repository(hash(permission.repository.name))
            repo.name = permission.repository.name
            repo.permission = permission.permission

            return repo

    def __log_session(self, session: dict):
        """
        helper function for keeping track of who calls what.
        """
        funcName = inspect.getouterframes(inspect.currentframe(), 2)[1][3]
        username = "Watcher"
        if session is not None:  # by default  None is the watcher
            username = session["user"]["user"]

        logging.debug(f"{username} called {funcName}")

    def __new__(cls):
        logging.debug("new bitbucket")
        return super().__new__(cls)

    def __del__(self):
        app_sccs.delete(self.hook_path)
