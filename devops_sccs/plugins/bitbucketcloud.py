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

import inspect
import logging
from contextlib import asynccontextmanager
from typing import Any, TypeAlias
from urllib.error import HTTPError

import requests
from atlassian.bitbucket import Cloud
from atlassian.bitbucket.cloud.repositories import Repository
from atlassian.errors import ApiNotFoundError, ApiPermissionError

from ..accesscontrol import Action, Permission
from ..ats_cache import ats_cache
from ..client import SccsClient, register_plugin
from ..errors import SccsException, TriggerCdEnvUnsupported
from ..plugin import SccsPlugin
from ..typing import cd as typing_cd
from ..typing import repositories as typing_repo
from ..utils import cd as utils_cd

PLUGIN_NAME = "bitbucketcloud"

Session: TypeAlias = Cloud | dict[str, Any]


class BitbucketCloud(SccsPlugin):
    hook_path = f"/{PLUGIN_NAME}/hooks/repo"

    async def init(self, core: SccsClient, config: dict):
        """
        Initialize the plugin
        """
        logging.info("Initializing BitbucketCloud plugin...")

        self.local_sessions: dict[str, Any] = {}

        self.team = config["team"]

        self.cd_environments: list[dict[str, Any]] = config["continuous_deployment"]["environments"]
        self.cd_branches_accepted: list[str] = [env["branch"] for env in self.cd_environments]
        self.cd_pullrequest_tag: str = config["continuous_deployment"]["pullrequest"]["tag"]
        self.cd_versions_available: list[str] = config["continuous_deployment"]["pipeline"]["versions_available"]

        self.watcherUser: str = config["watcher"]["user"]
        self.watcherPwd: str = config["watcher"]["pwd"]

        if config["watcher"] is not None:
            self.watcher = Cloud(username=self.watcherUser, password=self.watcherPwd, cloud=True)

        self.accesscontrol_rules: dict[int, list[str]] = {
            Action.WATCH_CONTINOUS_DEPLOYMENT_CONFIG: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE: Permission.READ_CAPABILITIES,
        }

        logging.info("Initialization complete!")

    async def cleanup(self):
        pass

    def get_session_id(self, session: dict[str, Any] | Cloud):
        """see plugin.py"""

        if isinstance(session, dict):
            session_id = hash((session["user"], session["apikey"]))
        elif isinstance(session, Cloud):
            session_id = hash((session.username, session.password))
        else:
            raise SccsException("Invalid session")

        logging.debug(f"Created session id: {session_id}")
        return session_id

    async def open_session(self, session_id, session: dict[str, Any] | Cloud):
        """see plugin.py"""

        existing_session = self.local_sessions.get(session_id)

        if existing_session is not None:
            if isinstance(existing_session, dict):
                existing_session["shared-session"] += 1
                logging.debug(f'Reusing existing session {session_id} (shared: {existing_session["shared-session"]})')
            return existing_session

        new_session: dict[str, Any] | Cloud
        if isinstance(session, dict):
            new_session = {
                "session_id": session_id,
                "shared-session": 1,
                "user": {
                    "user": session["user"],
                    "apikey": session["apikey"],
                    "team": self.team,
                    "author": session["author"],
                },
                "cache": {"repositories": {"values": [], "last_access": 0, "ttl": 7200}},
            }
        elif isinstance(session, Cloud):
            new_session = Cloud(username=session.username, password=session.password, cloud=True)

        self.local_sessions[session_id] = new_session

        return new_session

    async def close_session(self, session_id, session: dict[str, Any] | Cloud, args):
        """see plugin.py"""
        if isinstance(session, dict):
            try:
                session["shared-session"] -= 1

                logging.debug(f'Closing session {session_id} (shared: {session["shared-session"]})')

                if session["shared-session"] <= 0:
                    # not used anymore
                    logging.debug(f"Removing session {session_id} from local cache")
                    self.local_sessions.pop(session_id)
            except KeyError as e:
                logging.error(f"Error while closing session {session_id}: {e}")
                pass

    async def accesscontrol(self, session: dict[str, Any], repository, action, args):
        """see plugin.py"""
        logging.debug(f"Assessing access rights for {repository}")

        async with self.bitbucket_session(session) as bitbucket:
            # will raise ApiPermissionError if access is forbidden
            bitbucket.workspaces.get(self.team).repositories.get(repository)

    async def passthrough(self, session, request, args):
        return await super().passthrough(session, request, args)

    @ats_cache()
    async def get_repositories(self, session: dict[str, Any], args: Any = None) -> list[typing_repo.Repository]:
        """see plugin.py"""

        result: list[typing_repo.Repository] = []

        async with self.bitbucket_session(session) as bitbucket:
            permission_repos: dict[str, Any] | None = bitbucket.get(
                "user/permissions/repositories", params={"pagelen": 100}
            )  # type: ignore
            while True:
                if permission_repos is None:
                    return result

                repos: list[dict] = permission_repos["values"]
                next: str | None = permission_repos.get("next")

                if len(repos) == 0:
                    return result

                for repo in repos:
                    result.append(
                        typing_repo.Repository(
                            key=hash(repo["repository"]["name"]),
                            name=repo["repository"]["name"],
                            permission=repo["permission"],
                        )
                    )

                if next is None:
                    break
                try:
                    permission_repos = bitbucket.get(next, absolute=True)  # type: ignore
                except Exception:
                    break

        return result

    @ats_cache()
    async def get_repository(self, session, repository):
        """see plugin.py"""
        async with self.bitbucket_session(session) as bitbucket:
            try:
                return bitbucket.workspaces.get(self.team).repositories.get(repository, by="name")
            except ApiPermissionError:
                logging.warning(f'user "{session["user"]["user"]}" has no permission for "{repository}"')
                return None
            except Exception:
                logging.warning(f"repository {repository} not found")
                return None

    async def add_repository(self, session, provision, repository, template, template_params, args):
        pass

    @ats_cache(ttl=60)
    async def get_continuous_deployment_config(
        self, session, repository, environments=None, args=None
    ) -> list[typing_cd.EnvironmentConfig]:
        """
        fetch the version deployed in each environment
        """
        results: list[typing_cd.EnvironmentConfig] = []

        async with self.bitbucket_session(session, self.watcher) as bitbucket:
            deploys = []
            repo = bitbucket.workspaces.get(self.team).repositories.get(repository=repository)

            # Get supported branches
            for branch in repo.branches.each():
                try:
                    index = self.cd_branches_accepted.index(branch.name)
                    if environments is None or self.cd_environments[index]["name"] in environments:
                        deploys.append((branch.name, index))
                except ValueError:
                    pass

            # Do we have something to do ?
            if len(deploys) == 0:
                logging.info("continuous deployment seems not supported for {}".format(repository))
                return results

            # Ordered deploys
            deploys = sorted(deploys, key=lambda deploy: deploy[1])

            # Get continuous deployment config for all environments selected

            for branch_name, index in deploys:
                env_config = await self.get_continuous_deployment_config_by_branch(
                    repository, repo, branch_name, self.cd_environments[index]
                )
                results.append(env_config[1])

        logging.debug(results)
        return results

    @ats_cache()
    async def get_continuous_deployment_versions_available(
        self, session, repository, args=None
    ) -> list[typing_cd.Available]:
        """
        Get the list of version available to deploy
        """
        logging.info(f"_fetch_continuous_deployment_versions_available on repo : {repository}")

        self.__log_session(session)
        versions = []
        async with self.bitbucket_session(session, self.watcher) as bitbucket:

            repo = bitbucket.workspaces.get(self.team).repositories.get(repository=repository)

            logging.info(f"list cd version available : {self.cd_versions_available}")

            for pipeline in repo.pipelines.each(q="target.ref_name=master", sort="-created_on"):
                target = pipeline.get_data("target")
                state = pipeline.get_data("state")
                if target is None or state is None or target["type"] != "pipeline_ref_target":
                    continue
                ref_name = target["ref_name"]
                result_name = state["result"]["name"]
                if ref_name in self.cd_versions_available and result_name == "SUCCESSFUL":
                    available = typing_cd.Available(
                        key=hash((repository, pipeline.build_number)),
                        build=str(pipeline.build_number),
                        version=target["commit"]["hash"],
                    )
                    logging.debug(f"adding version available for build nb : {pipeline.build_number}")
                    versions.append(available)

        return versions

    async def trigger_continuous_deployment(
        self, session, repository, environment, version, args
    ) -> typing_cd.EnvironmentConfig:
        """
        Trigger a deployment in a specific environment
        """
        # Get Continuous Deployment configuration for the environment requested
        cd_environment_config: dict[str, Any] = {}
        for cd_environment in self.cd_environments:
            if cd_environment["name"] == environment:
                cd_environment_config = cd_environment
                break
        if len(cd_environment_config) == 0:
            utils_cd.trigger_not_supported(repository, environment)

        continuous_deployment = None
        # using user session for repo manipulations
        async with self.bitbucket_session(session) as bitbucket:
            # Check current configuration using the cache. This is ok because the user will see the
            # deployed version anyway
            list_continuous_deployment = await self.get_continuous_deployment_config(
                session=None,
                repository=repository,
                environments=[environment],
                args=args,
            )

            for config in list_continuous_deployment:
                if config.environment == environment:
                    continuous_deployment = config
                    break

            if continuous_deployment is None:
                logging.info(f"Continuous deployment config not found for {repository} on environment {environment}")
                raise TriggerCdEnvUnsupported(repository, environment)

            logging.info(f"Triggering new deploy on env : {environment} with version: {continuous_deployment.version}")

            versions_available = await self.get_continuous_deployment_versions_available(None, repository, args)

            utils_cd.trigger_prepare(
                continuous_deployment,
                versions_available,
                repository,
                environment,
                version,
            )

            # Check if we need/can do a PR
            repo = bitbucket.workspaces.get(self.team).repositories.get(repository=repository)
            branch = cd_environment_config["branch"]

            if cd_environment_config.get("trigger", {}).get("pullrequest", False):
                # Continuous Deployment is done with a PR.
                # We need to check if there is already one open (the version requested doesn't matter)
                for pullrequest in repo.pullrequests.each():
                    if (
                        pullrequest.destination_branch == branch
                        and pullrequest.title
                        and self.cd_pullrequest_tag in pullrequest.title
                    ):
                        link = pullrequest.get_link("html")
                        raise SccsException(
                            f"A continuous deployment request is already open. link: {link['href'] if link else None}"
                        )

                deploy_branch = repo.branches.get(name=branch)

                deploy_branch.name = f"continuous-deployment-{environment}"
                try:
                    # If the branch already exists, we should remove it.
                    repo.branches.delete(path=f"{deploy_branch.name}")
                except ApiNotFoundError:
                    pass
                await deploy_branch.create()
            else:
                deploy_branch = None

            requests.request(
                "POST",
                repo.url + "/src",
                data={
                    f'/{cd_environment_config["version"]["file"]}': f"{version}\n",
                    "message": f"deploy version {version}",
                    "author": session["user"]["author"],
                    "branch": branch if deploy_branch is None else deploy_branch.name,
                },
                auth=(bitbucket.username, bitbucket.password),  # type: ignore
            )

            if deploy_branch is not None:
                # Continuous Deployment is done with a PR.
                pr = repo.pullrequests.create(
                    title=f"Ugrade {environment} {self.cd_pullrequest_tag}",
                    source_branch=deploy_branch.name,
                    destination_branch=branch,
                    close_source_branch=True,
                )

                # race condition start here
                link = pr.get_link("html")
                continuous_deployment.pullrequest = link["href"] if link else None
            else:
                # Continuous Deployment done
                continuous_deployment.version = version

            # race condition finish after that statement.

            # Return the new configuration (new version or PR in progress)

        return continuous_deployment

    @ats_cache(ttl=60)
    async def get_continuous_deployment_environments_available(
        self, session, repository, args
    ) -> list[typing_cd.EnvironmentConfig]:
        result = await self.get_continuous_deployment_config(
            session=session, repository=repository, environments=None, args=args
        )
        logging.debug(f"get_continuous_deployment_environments_available on repo : {repository} --- result : {result}")
        return result

    async def bridge_repository_to_namespace(self, session, repository, environment, untrustable, args):
        return await super().bridge_repository_to_namespace(session, repository, environment, untrustable, args)

    async def compliance(self, session, remediation, report, args):
        return await super().compliance(session, remediation, report, args)

    async def compliance_report(self, session, args):
        return await super().compliance_report(session, args)

    async def compliance_repository(self, session, repository, remediation, report, args):
        return await super().compliance_repository(session, repository, remediation, report, args)

    async def compliance_report_repository(self, session, repository, args):
        return await super().compliance_report_repository(session, repository, args)

    async def get_hooks_repository(self, session, repository, args):
        return await super().get_hooks_repository(session, repository, args)

    ###########################################################
    # The following methods aren't present in the superclass. #
    ###########################################################

    def create_continuous_deployment_config_by_branch(
        self,
        repository: str,
        version: str,
        branch: str,
        config: dict,
        pullrequest: str | None = None,
        buildStatus: str = "SUCCESSFUL",
    ) -> typing_cd.EnvironmentConfig:
        """
        Helper function to standarise the creation of EnvironementConfig
        """
        trigger_config = config.get("trigger", {})
        env = typing_cd.EnvironmentConfig(
            key=hash((repository, branch)),
            version=version,
            environment=config["name"],
            readonly=not trigger_config.get("enabled", True),
            pullrequest=pullrequest if trigger_config.get("pullrequest", False) else None,
        )
        return env

    async def get_continuous_deployment_config_by_branch(
        self, repository: str, repo: Repository, branch_name: str, config: dict
    ) -> tuple[str, typing_cd.EnvironmentConfig]:
        """
        Get environment configuration for a specific branch
        """
        logging.debug(f"Getting continuous deployment config for '{repository}' on branch '{branch_name}'")
        # Get version
        file_version = config["version"].get("file")
        commit_hash = repo.branches.get(branch_name).hash
        version: str
        if file_version is not None:
            try:
                res: bytes | None = repo.get(
                    path=f"src/{commit_hash}/{file_version}",
                    not_json_response=True,
                )
                if res is not None:
                    version = res.decode("utf-8")
                else:
                    raise SccsException(f"failed to get version from {file_version} for {repository} on {branch_name}")
            except Exception:
                raise SccsException(f"failed to get version from {file_version} for {repository} on {branch_name}")
        elif config["version"].get("git") is not None:
            version = commit_hash
        else:
            raise NotImplementedError()

        trigger_config = config.get("trigger", {})
        pullrequest_link = None
        if trigger_config.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            for pullrequest in repo.pullrequests.each():
                if (
                    pullrequest.destination_branch == config["branch"]
                    and pullrequest.title
                    and self.cd_pullrequest_tag in pullrequest.title
                ):
                    link = pullrequest.get_link("html")
                    pullrequest_link = link["href"] if type(link) is dict else link
                    break

        return (
            branch_name,
            self.create_continuous_deployment_config_by_branch(
                repository, version, branch_name, config, pullrequest_link
            ),
        )

    @ats_cache()
    async def get_repository_permission(self, session: dict[str, Any], repo_name: str) -> str | None:
        async with self.bitbucket_session(session=session) as bitbucket:
            # get repository permissions for user
            try:
                res: dict = bitbucket.get(
                    "user/permissions/repositories",
                    params={"repository.name": repo_name},
                )
                values = res.get("values", None) if res is not None else None
                if values is not None and len(values) > 0:
                    return values[0]["permission"]
                else:
                    return None
            except HTTPError as e:
                logging.warning(f"error getting repository permissions: {e}")
                return None

    @ats_cache()
    async def get_projects(self, session):
        """Return a list of projects"""
        async with self.bitbucket_session(session) as bitbucket_session:
            return bitbucket_session.get(f"/2.0/workspaces/{self.team}/projects")

    @ats_cache()
    async def get_webhook_subscriptions(self, session, repo_name):
        async with self.bitbucket_session(session) as bitbucket:
            repo = bitbucket.workspaces.get(self.team).repositories.get(repository=repo_name)
            return repo.get(path="hooks")

    async def create_webhook_subscription(
        self,
        session,
        repo_name,
        url,
        active,
        events,
        description,
    ):
        async with self.bitbucket_session(session) as bitbucket:
            repo = bitbucket.workspaces.get(self.team).repositories.get(repository=repo_name)
            return repo.post(
                path="hooks",
                json={
                    "url": url,
                    "active": active,
                    "events": events,
                    "description": description,
                },
            )

    async def delete_webhook_subscription(self, session, repo_name, subscription_id) -> None:
        async with self.bitbucket_session(session) as bitbucket:
            repo = bitbucket.workspaces.get(self.team).repositories.get(repository=repo_name)
            repo.request(
                method="DELETE",
                path=f"hooks/{subscription_id}",
            )

    @asynccontextmanager
    async def bitbucket_session(
        self,
        session: Session | None,
        default_session: Cloud | None = None,
    ):
        # Use default session if session is not provided (mainly used for watch requests with prior accesscontrol calls)
        if session is None:
            if default_session is None:
                raise SccsException("No session provided and no default session")
            yield default_session
            return

        if isinstance(session, Cloud):
            yield session
        elif isinstance(session, dict):
            # Regular flow
            bitbucket = Cloud(
                username=session["user"]["user"],
                password=session["user"]["apikey"],
                cloud=True,
            )
            try:
                yield bitbucket
            finally:
                bitbucket.close()

    def get_session(self, session_id) -> dict[str, Any] | None:
        return self.local_sessions.get(session_id)

    def __log_session(self, session: Session | None):
        """
        helper function for keeping track of who calls what.
        """
        funcName = inspect.getouterframes(inspect.currentframe(), 2)[1][3]
        username = "Watcher"

        if isinstance(session, dict):  # by default  None is the watcher
            username = session["user"]["user"]

        logging.debug(f"{username} called {funcName}")

    def __new__(cls):
        logging.debug("new bitbucket")
        return super().__new__(cls)

    def __del__(self):
        pass


register_plugin(PLUGIN_NAME, BitbucketCloud())
