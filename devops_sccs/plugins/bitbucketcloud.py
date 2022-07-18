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
from functools import partial
import inspect
import logging
import time
from contextlib import asynccontextmanager
from tkinter import N
from typing import Any, Dict, Generator, List, TypeAlias, overload

from aiobitbucket.apis.repositories.repository import RepoSlug
from aiobitbucket.bitbucket import Bitbucket
from aiobitbucket.errors import NetworkNotFound
from aiobitbucket.typing.refs import Branch

from ..accesscontrol import AccessForbidden, Action, Permission
from ..atscached import atscached
from ..core import Core
from ..errors import SccsException
from ..plugin import Sccs
from ..realtime.hookserver import app_sccs
from ..typing import cd as typing_cd
from ..typing import repositories as typing_repo
from ..utils import cd as utils_cd

PLUGIN_NAME = "bitbucketcloud"


def init_plugin():
    if BitbucketCloud._instance is None:
        BitbucketCloud._instance = BitbucketCloud()
    return PLUGIN_NAME, BitbucketCloud._instance


Session: TypeAlias = Bitbucket | Dict[str, Any]


class BitbucketCloud(Sccs):
    hook_path = f"/{PLUGIN_NAME}/hooks/repo"
    _instance: BitbucketCloud | None = None

    async def init(self, core: Core, args):
        """
        Initialize the plugin
        """

        self.local_sessions: Dict[str, Any] = {}

        self.team = args["team"]

        self.cd_environments: List[Dict[str, Any]] = args["continuous_deployment"][
            "environments"
        ]
        self.cd_branches_accepted: List[str] = [
            env["branch"] for env in self.cd_environments
        ]
        self.cd_pullrequest_tag: str = args["continuous_deployment"]["pullrequest"][
            "tag"
        ]
        self.cd_versions_available: List[str] = args["continuous_deployment"][
            "pipeline"
        ]["versions_available"]

        self.watcherUser: str = args["watcher"]["user"]
        self.watcherPwd: str = args["watcher"]["pwd"]

        if args["watcher"] is not None:
            self.watcher = Bitbucket()
            self.watcher.open_basic_session(self.watcherUser, self.watcherPwd)

        self.accesscontrol_rules: Dict[int, list[str]] = {
            Action.WATCH_CONTINOUS_DEPLOYMENT_CONFIG: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE: Permission.READ_CAPABILITIES,
        }

        BitbucketCloud._instance = self

    def get_session_id(self, args):
        """see plugin.py"""

        session_id = hash((args["user"], args["apikey"]))

        logging.debug(f"get session id: {session_id}")
        return session_id

    async def open_session(self, session_id, args):
        """see plugin.py"""

        existing_session = self.local_sessions.get(session_id)

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
            "cache": {"repositories": {"values": [], "last_access": 0, "ttl": 7200}},
        }

        self.local_sessions[session_id] = new_session

        return new_session

    def get_session(self, session_id) -> Dict[str, Any] | None:
        return self.local_sessions.get(session_id)

    async def close_session(self, session_id, session, args):
        """see plugin.py"""

        session["shared-session"] -= 1

        logging.debug(
            f'close session {session_id} (shared: {session["shared-session"]})'
        )

        if session["shared-session"] <= 0:
            # not used anymore
            logging.debug(f"remove session {session_id} from cache")
            self.local_sessions.pop(session_id)

    @asynccontextmanager
    async def bitbucket_session(
        self,
        session: Bitbucket | Dict[str, Any] | None,
        default_session: Bitbucket | None = None,
    ):
        # Use default session if session is not provided (mainly used for watch requests with prior accesscontrol calls)
        if session is None:
            if default_session is None:
                raise SccsException("No session provided and no default session")
            yield default_session
            return

        if isinstance(session, Bitbucket):
            yield session
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

    async def accesscontrol(self, session: Dict[str, Any], repository, action, args):
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

    @atscached()
    async def get_repositories(self, session: Dict[str, Any], args: Any = None) -> list:
        return await self._get_repositories(session, args)

    async def _get_repositories(self, session: Dict[str, Any], args: Any) -> list:
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
        session["cache"]["repositories"]["values"] = result
        session["cache"]["repositories"]["last_access"] = time.time()

        return result

    @atscached()
    async def get_repository(self, session, repository):
        return await super().get_repository(session, repository)

    async def _get_repository(
        self, session: Dict[str, Any], repository
    ) -> typing_repo.Repository:
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

    @atscached()
    async def get_continuous_deployment_config_by_branch(
        self, repository: str, repo: RepoSlug, branch: Branch, config: dict
    ) -> tuple[str, typing_cd.EnvironmentConfig]:
        return await self._get_continuous_deployment_config_by_branch(
            repository, repo, branch, config
        )

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

    async def get_continuous_deployment_config(
        self, session, repository, environments, args
    ):
        return await self.fetch_continuous_deployment_config(
            session=session, repository=repository
        )

    @atscached()
    async def fetch_continuous_deployment_config(
        self, repository, session=None, environment=None
    ):
        return await self._fetch_continuous_deployment_config(
            repository, session, environment
        )

    async def _fetch_continuous_deployment_config(
        self, repository, session=None, environments=None
    ) -> List:
        """
        fetch the version deployed in each environment
        """
        results = []

        async with self.bitbucket_session(session, self.watcher) as bitbucket:
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
                    "continuous deployment seems not supported for {}".format(
                        repository
                    )
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
            for task in task_results:
                try:
                    [branch, results] = task
                    response[branch] = results
                except AttributeError as e:
                    logging.error(e)

            results = [response[branch] for branch in response]
            logging.debug(
                f"_fetch_continuous_deployment_config for {repository} result is : {results}"
            )
        return results

    @atscached()
    async def fetch_continuous_deployment_environments_available(
        self, repository, session=None
    ):
        return await self._fetch_continuous_deployment_environments_available(
            repository, session
        )

    async def _fetch_continuous_deployment_environments_available(
        self, repository, session=None
    ) -> List:
        """
        fetch the available environements for the specified repository.
        """
        self.__log_session(session)
        logging.debug(
            f"_fetch_continuous_deployment_environments_available on repo : {repository}"
        )
        environments = []
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
            environments = [
                env for env, _ in sorted(availables, key=lambda available: available[1])
            ]
            logging.debug(
                f"_fetch_continuous_deployment_environments_available : {environments}"
            )
        return environments

    async def get_continuous_deployment_environments_available(
        self, session, repository, args
    ) -> list:
        result = await self.get_continuous_deployment_config(
            session=session, repository=repository, environments=None, args=args
        )
        logging.debug(
            f"get_continuous_deployment_environments_available on repo : {repository} --- result : {result}"
        )
        return result

    @atscached()
    async def fetch_continuous_deployment_versions_available(
        self, repository, session=None, args=None
    ) -> list:
        return await self._fetch_continuous_deployment_versions_available(
            repository, session, args=args
        )

    async def _fetch_continuous_deployment_versions_available(
        self, repository, session=None, args=None
    ) -> list:
        """
        Get the list of version available to deploy
        """
        logging.info(
            f"_fetch_continuous_deployment_versions_available on repo : {repository}"
        )

        self.__log_session(session)
        versions = []
        async with self.bitbucket_session(session, self.watcher) as bitbucket:

            repo = bitbucket.repositories.repo_slug(self.team, repository)

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
                    versions.append(available)

        return versions

    async def get_continuous_deployment_versions_available(
        self, session, repository, args
    ) -> list:
        result = await self.fetch_continuous_deployment_versions_available(
            session=session, repository=repository, args=args
        )
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
        cd_environment_config: Dict[str, Any] = {}
        for cd_environment in self.cd_environments:
            if cd_environment["name"] == environment:
                cd_environment_config = cd_environment
                break
        if len(cd_environment_config) == 0:
            utils_cd.trigger_not_supported(repository, environment)

        # using user session for repo manipulations
        async with self.bitbucket_session(session) as bitbucket:
            # Check current configuration using the cache. This is ok because the user will see that deployed version anyway
            list_continuous_deployment = await self.get_continuous_deployment_config(
                session=None,
                repository=repository,
                environments=[environment],
                args=args,
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

            # Return the new configuration (new version or PR in progress)
            return continuous_deployment

    @atscached()
    async def get_webhook_subscriptions(self, session, repo_name):
        async with self.bitbucket_session(session) as bitbucket:
            return await bitbucket.webhooks.get_by_repository_name(
                workspace=self.team, repo_name=repo_name
            )

    async def create_webhook_subscription(
        self,
        session,
        repo_name,
        url,
        active,
        events,
        description,
    ):
        subscription = None
        async with self.bitbucket_session(session) as bitbucket:
            subscription = await bitbucket.webhooks.create_subscription(
                workspace=self.team,
                repo_name=repo_name,
                url=url,
                active=active,
                events=events,
                description=description,
            )

        return subscription

    async def delete_webhook_subscription(
        self, session, repo_name, subscription_id
    ) -> None:
        async with self.bitbucket_session(session) as bitbucket:
            await bitbucket.webhooks.delete_subscription(
                workspace=self.team,
                repo_name=repo_name,
                subscription_id=subscription_id,
            )

    def __log_session(self, session: Session | None):
        """
        helper function for keeping track of who calls what.
        """
        funcName = inspect.getouterframes(inspect.currentframe(), 2)[1][3]
        username = "Watcher"

        if isinstance(session, Dict):  # by default  None is the watcher
            username = session["user"]["user"]

        logging.debug(f"{username} called {funcName}")

    def __new__(cls):
        logging.debug("new bitbucket")
        return super().__new__(cls)

    def __del__(self):
        app_sccs.delete(self.hook_path)
