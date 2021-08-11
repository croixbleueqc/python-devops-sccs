# Copyright 2020-2021 Croix Bleue du Québec

# This file is part of python-devops-sccs.

# python-devops-sccs is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# python-devops-sccs is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with python-devops-sccs.  If not, see <https://www.gnu.org/licenses/>.

import asyncio
import logging
import time

from contextlib import asynccontextmanager
from aiobitbucket.bitbucket import Bitbucket
from aiobitbucket.typing.refs import Branch
from aiobitbucket.apis.repositories.repository import RepoSlug
from aiobitbucket.errors import NetworkNotFound
from ..plugin import Sccs
from ..errors import SccsException
from ..accesscontrol import AccessForbidden, Actions, Permissions
from ..utils import cd as utils_cd

from ..typing import cd as typing_cd
from ..typing import repositories as typing_repo

PLUGIN_NAME="bitbucketcloud"

def init_plugin():
    return PLUGIN_NAME, BitbucketCloud()

class BitbucketCloud(Sccs):
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
        
        self.watcher = Bitbucket()
        self.watcher.open_basic_session(args["watcher"]["user"], args["watcher"]["pwd"])

        self.accesscontrol_rules = {
            Actions.WATCH_CONTINOUS_DEPLOYMENT_CONFIG: Permissions.READ_CAPABILITIES,
            Actions.WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE: Permissions.READ_CAPABILITIES,
            Actions.WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE: Permissions.READ_CAPABILITIES
        }

    async def cleanup(self):
        await self.watcher.close_session()

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

        async with self.bitbucket_session(session) as bitbucket:
            permission = await bitbucket.user.permissions.repositories.get_by_full_name(self.team + "/" + repository)
            repo = typing_repo.Repository(hash(permission.repository.name))
            repo.name = permission.repository.name
            repo.permission = permission.permission

            return repo

    async def _get_continuous_deployment_config_by_branch(self, repository: str, repo: RepoSlug, branch: Branch, config: dict) -> typing_cd.EnvironmentConfig:
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

        env = typing_cd.EnvironmentConfig(hash((repository, branch.name)))
        env.version = version
        env.environment = config["name"]

        trigger_config = config.get("trigger", {})
        env.readonly = not trigger_config.get("enabled", True)

        if trigger_config.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            async for pullrequest in repo.pullrequests().get():
                if pullrequest.destination.branch.name == config["branch"] and self.cd_pullrequest_tag in pullrequest.title:
                    env.pullrequest = pullrequest.links.html.href
                    break

        return env

    async def get_continuous_deployment_config(self, session, repository, environments=None, args=None):
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

        response = []
        for result in results:
            response.append(result)

        return response

    async def get_continuous_deployment_environments_available(self, session, repository, args) -> list:
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

            return response

    async def get_continuous_deployment_versions_available(self, session, repository, args) -> list:
        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            # commits available to be deployed
            repo = bitbucket.repositories.repo_slug(self.team, repository)

            response = []

            async for pipeline in repo.pipelines().get(filter='sort=-created_on'):
                if pipeline.target.ref_name in self.cd_versions_available and \
                    pipeline.state.result.name == "SUCCESSFUL":

                    available = typing_cd.Available(hash((repository, pipeline.build_number)))
                    available.build = pipeline.build_number
                    available.version = pipeline.target.commit.hash
                    response.append(available)

            return response

    async def trigger_continuous_deployment(self, session, repository, environment, version, args) -> typing_cd.EnvironmentConfig:
        """see plugin.py"""

        logging.debug(f"trigger for {repository} on {environment}")

        # Get Continuous Deployment configuration for the environment requested
        cd_environment_config = None
        for cd_environment in self.cd_environments:
            if cd_environment["name"] == environment:
                cd_environment_config = cd_environment
                break
        if cd_environment_config is None:
            utils_cd.trigger_not_supported(repository, environment)

        async with self.bitbucket_session(session) as bitbucket:
            # Check all configurations
            continuous_deployment = (await self.get_continuous_deployment_config(session, repository, environments=[environment]))[0]
            versions_available = await self.get_continuous_deployment_versions_available(session, repository, args)
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
                #If the branch already exist , we should remove it.
                try:
                      deploy_branch.delete()
                except NetworkNotFound :
                        pass
                deploy_branch.name = f"continuous-deployment-{environment}"
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

            if deploy_branch is not None:
                # Continuous Deployment is done with a PR.
                pr = repo.pullrequests().new()
                pr.title = f"Ugrade {environment} {self.cd_pullrequest_tag}"
                pr.close_source_branch = True
                pr.source.branch.name = deploy_branch.name
                pr.destination.branch.name = branch
                await pr.create()
                await pr.get()
                continuous_deployment.pullrequest = pr.links.html.href
            else:
                # Continuous Deployment done
                continuous_deployment.version = version

            # Return the new configuration (new version or PR in progress)
            return continuous_deployment
