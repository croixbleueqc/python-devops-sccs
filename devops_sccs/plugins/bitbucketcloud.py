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
from urllib.error import HTTPError

import requests
from atlassian.bitbucket import Cloud
from atlassian.bitbucket.cloud.repositories import Repository
from atlassian.bitbucket.cloud.workspaces import Workspace
from atlassian.errors import ApiNotFoundError

from devops_console.schemas import WebhookEvent
from devops_sccs.schemas.config import Environment, PluginConfig
from ..accesscontrol import Action, Permission
from ..ats_cache import ats_cache
from ..client import register_plugin, SccsClient
from ..errors import SccsException, TriggerCdEnvUnsupported
from ..plugin import SccsApi, StoredSession
from ..provision import Provision
from ..typing import cd as typing_cd, repositories as typing_repo
from ..typing.credentials import Credentials
from ..utils import cd as utils_cd
from ..utils.aioify import run_async

PLUGIN_NAME = "bitbucketcloud"


class BitbucketCloud(SccsApi):
    async def init(self, core: SccsClient, config: PluginConfig):
        """
        Initialize the plugin
        """
        logging.info("Initializing BitbucketCloud plugin...")

        self.local_sessions: dict[int, StoredSession] = {}

        self.team = config.team

        self.cd_environments = config.continuous_deployment.environments
        self.cd_branches_accepted = [env.branch for env in self.cd_environments]
        self.cd_pullrequest_tag = config.continuous_deployment.pullrequest.tag
        self.cd_versions_available = config.continuous_deployment.pipeline.versions_available

        try:
            self.watcher_user = Credentials(
                user=config.watcher.user,
                author="Admin User",
                apikey=config.watcher.pwd,
            )
            self.watcher = Cloud(
                username=self.watcher_user.user,
                password=self.watcher_user.apikey,
                cloud=True,
            )
        except KeyError:
            logging.error("Watcher credentials are missing from the configuration file.")
            raise

        self.accesscontrol_rules = {
            Action.WATCH_CONTINOUS_DEPLOYMENT_CONFIG: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE: Permission.READ_CAPABILITIES,
            Action.WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE: Permission.READ_CAPABILITIES,
        }

        logging.info("Initialization complete!")

    async def cleanup(self):
        pass

    def get_session_id(self, credentials: Credentials | Cloud | None) -> int:
        if credentials is None:
            return hash((self.watcher.username, self.watcher.password))
        elif isinstance(credentials, Cloud):
            return hash((credentials.username, credentials.password))
        elif isinstance(credentials, Credentials):
            return hash((credentials.user, credentials.apikey))

        raise SccsException("Invalid credentials")

    async def open_session(
            self, session_id: int, credentials: Credentials | None = None
    ) -> StoredSession:
        existing_session = self.local_sessions.get(session_id)

        if existing_session is not None:
            existing_session.shared_sessions += 1
            return existing_session

        stored = StoredSession(
            id=session_id,
            shared_sessions=1,
            session=Cloud(username=credentials.user, password=credentials.apikey, cloud=True)
            if credentials is not None
            else self.watcher,
            credentials=credentials if credentials is not None else self.watcher_user,
        )

        self.local_sessions[session_id] = stored

        return stored

    async def close_session(
            self,
            session_id: int,
    ):
        session = await self.get_stored_session(session_id)
        if session is not None:
            try:
                session.shared_sessions -= 1

                logging.debug(f"Closing session {session_id} (shared: {session.shared_sessions})")

                if session.shared_sessions <= 0:
                    # not used anymore
                    logging.debug(f"Removing session {session_id} from local cache")
                    self.local_sessions.pop(session_id)
            except Exception as e:
                logging.error(f"Error while closing session {session_id}: {e}")
                pass

    async def get_stored_session(
            self, session_id: int | None, session: Cloud | None = None
    ) -> StoredSession | None:
        if session_id is None:
            if session is not None:
                session_id = self.get_session_id(session)
            else:
                raise SccsException("Must specify a session or a session_id")

        return self.local_sessions.get(session_id)

    @staticmethod
    def __log_session(session: Cloud | None):
        """
        helper function for keeping track of who calls what.
        """
        funcName = inspect.getouterframes(inspect.currentframe(), 2)[1][3]
        username = session.username if session is not None else "Watcher"

        logging.debug(f"{username} called {funcName}")

    def __new__(cls):
        return super().__new__(cls)

    async def accesscontrol(self, session: Cloud, repo_name: str, action: int):
        """see plugin.py"""
        logging.debug(f"Assessing access rights for {repo_name}")

        # will raise ApiPermissionError if access is forbidden
        permissions: dict = await run_async(
            session.get,
            "user/permissions/repositories",
            params=[("q", f'repository.name="{repo_name}"')],
        )  # type: ignore
        logging.debug(f'Permissions for {repo_name}: {permissions["values"][0]["permission"]}')

    async def passthrough(self, session: Cloud, request):
        return await super().passthrough(session, request)

    @ats_cache(
        miss_callback=lambda f: logging.debug(f"{f}: fetching..."),
    )
    async def get_repositories(
            self,
            session: Cloud,
    ) -> list[typing_repo.Repository]:
        """see plugin.py"""

        def get_repos_sync():
            result: list[typing_repo.Repository] = []
            for repo in session._get_paged(
                    "user/permissions/repositories", params={"pagelen": 100}
            ):
                assert isinstance(repo, dict)
                result.append(
                    typing_repo.Repository(
                        key=hash(repo["repository"]["name"]),
                        name=repo["repository"]["name"],
                        permission=repo["permission"],
                    )
                )
            return result

        return await run_async(get_repos_sync)

    @ats_cache()
    async def api_workspaces(self, session: Cloud) -> Workspace:
        return await run_async(session.workspaces.get, self.team)

    @ats_cache()
    async def get_repository(self, session: Cloud, repo_name: str) -> typing_repo.Repository | None:
        """see plugin.py"""
        repos = await self.get_repositories(session)
        for repo in repos:
            if repo.name == repo_name:
                return repo
        return None

    @ats_cache()
    async def get_api_repository(self, session: Cloud, repo_name: str) -> Repository | None:
        """see plugin.py"""

        workspace = await self.api_workspaces(session)

        return await run_async(workspace.repositories.get, repo_name)

    async def add_repository(
            self,
            session: Cloud,
            provision: Provision,
            repo_definition: dict,
            template: str,
            template_params: dict,
    ):
        return await run_async(
            super().add_repository, session, provision, repo_definition, template, template_params
        )

    @ats_cache(
        ttl=60.0,
        miss_callback=lambda f: logging.info(f"{f}: fetching..."),
    )
    async def get_continuous_deployment_config(
            self,
            session: Cloud | None,
            repo_name: str,
            environments=None,
    ) -> list[typing_cd.EnvironmentConfig]:
        """
        fetch the version deployed in each environment
        """
        if environments is None:
            environments = []
        if session is None:
            session = self.watcher

        results: list[typing_cd.EnvironmentConfig] = []

        repo = await self.get_api_repository(session, repo_name)
        if repo is None:
            return results

        def get_deploys_sync():
            deploys = []
            # Get supported branches
            for branch in repo.branches.each():
                try:
                    index = self.cd_branches_accepted.index(branch.name)
                    if len(environments) == 0 or self.cd_environments[index].name in environments:  # type: ignore
                        deploys.append((branch.name, index))
                except (KeyError, ValueError):
                    pass

            if len(deploys) == 0:
                raise SccsException(f"Continuous deployment not supported for {repo_name}")

            deploys.sort(key=lambda x: x[1])

            return deploys

        deploys = await run_async(get_deploys_sync)

        for branch, index in deploys:
            env_config = await self.get_continuous_deployment_config_by_branch(
                repo_name, repo, branch_name=branch, config=self.cd_environments[index]
            )
            results.append(env_config[1])

        logging.debug(results)
        return results

    @ats_cache(
        miss_callback=lambda f: logging.info(f"{f}: fetching..."),
    )
    async def get_continuous_deployment_versions_available(
            self, session: Cloud | None, repo_name: str
    ) -> list[typing_cd.Available]:
        """
        Get the list of version available to deploy
        """
        if session is None:
            session = self.watcher

        self.__log_session(session)
        versions: list[typing_cd.Available] = []

        repo = await self.get_api_repository(session, repo_name)
        if repo is None:
            return versions

        logging.info(f"list cd version available : {self.cd_versions_available}")

        def get_versions_sync():
            for pipeline in repo.pipelines.each(q="target.ref_name=master", sort="-created_on"):
                target = pipeline.get_data("target")
                state = pipeline.get_data("state")
                if target is None or state is None or target["type"] != "pipeline_ref_target":
                    continue
                ref_name = target["ref_name"]
                result_name = state["result"]["name"]
                if ref_name in self.cd_versions_available and result_name == "SUCCESSFUL":
                    available = typing_cd.Available(
                        key=hash((repo_name, pipeline.build_number)),
                        build=str(pipeline.build_number),
                        version=target["commit"]["hash"],
                    )
                    logging.debug(
                        f"adding version available for build nb : {pipeline.build_number}"
                    )
                    versions.append(available)

            return versions

        return await run_async(get_versions_sync)

    async def trigger_continuous_deployment(
            self, session: Cloud, repo_name: str, environment: str, version: str
    ) -> typing_cd.EnvironmentConfig:
        """
        Trigger a deployment in a specific environment
        """

        # Get Continuous Deployment configuration for the environment requested
        cd_environment_config: Environment
        for cd_environment in self.cd_environments:
            if cd_environment.name == environment:
                cd_environment_config = cd_environment
                break
        else:
            utils_cd.trigger_not_supported(repo_name, environment)

        # using user session for repo manipulations
        # Check current configuration using the cache. This is ok because the user will see the
        # deployed version anyway
        continuous_deployment: typing_cd.EnvironmentConfig
        try:
            continuous_deployment = (
                await self.get_continuous_deployment_config(
                    session=self.watcher,
                    repo_name=repo_name,
                    environments=[environment],
                )
            )[0]
        except IndexError:
            logging.info(
                f"Continuous deployment config not found for {repo_name} on environment {environment}"
            )
            raise TriggerCdEnvUnsupported(repo_name, environment)

        logging.info(
            f"Triggering new deploy on env : {environment} with version: {continuous_deployment.version}"
        )

        versions_available = await self.get_continuous_deployment_versions_available(
            self.watcher, repo_name
        )

        utils_cd.trigger_prepare(
            continuous_deployment, versions_available, repo_name, environment, version
        )

        repo = await self.get_api_repository(session, repo_name)
        if repo is None:
            raise TriggerCdEnvUnsupported(repo_name, environment)

        branch = cd_environment_config.branch

        if cd_environment_config.trigger.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            # We need to check if there is already one open (the version requested doesn't matter)
            def check_pr_sync():
                for pullrequest in repo.pullrequests.each():
                    if (
                            pullrequest.destination_branch == branch
                            and pullrequest.title is not None
                            and self.cd_pullrequest_tag in pullrequest.title
                    ):
                        link = pullrequest.get_link("html")
                        raise SccsException(
                            f"A continuous deployment request is already open. link: {link['href'] if link else None}"
                        )

            await run_async(check_pr_sync)

            deploy_branch = await run_async(repo.branches.get, name=branch)

            deploy_branch.name = f"continuous-deployment-{environment}"
            try:
                # If the branch already exists, we should remove it.
                await run_async(repo.branches.delete, path=f"{deploy_branch.name}")
            except ApiNotFoundError:
                pass
            await run_async(deploy_branch.create)
        else:
            deploy_branch = None

        author = await self.get_session_author(session)

        # see https://github.com/atlassian-api/atlassian-python-api/issues/1045#issue-1368184335
        # for the reason why we need to use the requests library directly
        await run_async(
            requests.request,
            "POST",
            repo.url + "/src",
            data={
                f'/{cd_environment_config.version["file"]}': f"{version}\n",
                "message": f"deploy version {version}",
                "author": author,
                "branch": branch if deploy_branch is None else deploy_branch.name,
            },
            auth=(session.username, session.password),  # type: ignore
        )

        if deploy_branch is not None:
            # Continuous Deployment is done with a PR.
            pr = await run_async(
                repo.pullrequests.create,
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

    @ats_cache(
        miss_callback=lambda f: logging.info(f"{f}: fetching..."),
    )
    async def get_continuous_deployment_environments_available(
            self, session: Cloud | None, repo_name
    ) -> list[typing_cd.EnvironmentConfig]:
        if session is None:
            session = self.watcher

        envs: list[typing_cd.EnvironmentConfig] = []

        repo = await self.get_api_repository(session, repo_name)
        if repo is None:
            return envs

        for environment in self.cd_environments:
            try:
                (_, cfg) = await self.get_continuous_deployment_config_by_branch(
                    repo_name,
                    repo=repo,
                    branch_name=environment.branch,
                    config=environment,
                )
            except Exception:
                continue
            envs.append(cfg)

        logging.debug(f"{envs}")
        return envs

    async def bridge_repository_to_namespace(
            self, session: Cloud, repo_name: str, environment: str, untrustable: bool
    ):
        return await super().bridge_repository_to_namespace(
            session, repo_name, environment, untrustable
        )

    async def compliance(self, session: Cloud, remediation: bool, report: bool) -> dict | None:
        return await super().compliance(session, remediation, report)

    async def compliance_report(self, session: Cloud) -> dict:
        return await super().compliance_report(session)

    async def compliance_repository(
            self, session: Cloud, repository, remediation, report
    ) -> dict | None:
        return await super().compliance_repository(session, repository, remediation, report)

    async def compliance_report_repository(self, session: Cloud, repository) -> dict:
        return await super().compliance_report_repository(session, repository)

    ###########################################################
    # The following methods aren't present in the superclass. #
    ###########################################################

    @staticmethod
    def create_continuous_deployment_config_by_branch(
            repository: str,
            version: str,
            branch: str,
            config: Environment,
            pullrequest: str | None = None,
            # buildStatus: str = "SUCCESSFUL",
    ) -> typing_cd.EnvironmentConfig:
        """
        Helper function to standardize the creation of EnvironmentConfig
        """
        trigger_config = config.trigger
        env = typing_cd.EnvironmentConfig(
            key=hash((repository, branch)),
            version=version,
            environment=config.name,
            readonly=not trigger_config.get("enabled", True),
            pullrequest=pullrequest if trigger_config.get("pullrequest", False) else None,
        )
        return env

    async def get_continuous_deployment_config_by_branch(
            self, repository: str, repo: Repository, branch_name: str, config: Environment
    ) -> tuple[str, typing_cd.EnvironmentConfig]:
        """
        Get environment configuration for a specific branch
        """
        logging.debug(
            f"Getting continuous deployment config for '{repository}' on branch '{branch_name}'"
        )
        # Get version
        version_file = config.version.get("file")
        commit_hash = (await run_async(repo.branches.get, branch_name)).hash
        version: str
        if version_file is not None:
            try:
                res: bytes | None = await run_async(
                    repo.get,
                    path=f"src/{commit_hash}/{version_file}",
                    not_json_response=True,
                )  # type: ignore
                if res is not None:
                    version = res.decode("utf-8")
                else:
                    raise SccsException(
                        f"failed to get version from {version_file} for {repository} on {branch_name}"
                    )
            except Exception:
                raise SccsException(
                    f"failed to get version from {version_file} for {repository} on {branch_name}"
                )
        elif config.version.get("git", False):
            version = commit_hash  # basically only the master branch
        else:
            raise NotImplementedError()

        pullrequest_link = None

        if config.trigger.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            def pr_sync():
                nonlocal pullrequest_link
                for pullrequest in repo.pullrequests.each():
                    if (
                            pullrequest.destination_branch == config.branch
                            and self.cd_pullrequest_tag in (pullrequest.title or "")
                    ):
                        link = pullrequest.get_link("html")
                        pullrequest_link = link["href"] if type(link) is dict else link
                        break

            await run_async(pr_sync)

        return (
            branch_name,
            BitbucketCloud.create_continuous_deployment_config_by_branch(
                repository, version, branch_name, config, pullrequest_link
            ),
        )

    @ats_cache()
    async def get_repository_permission(self, session: Cloud, repo_name: str) -> str | None:
        # get repository permissions for user
        try:
            res = await run_async(
                session.get,
                "user/permissions/repositories",
                params={"repository.name": repo_name},
            )
            assert isinstance(res, dict)
            values = res.get("values", None) if res is not None else None
            if values is not None and len(values) > 0:
                return values[0]["permission"]
            else:
                return None
        except HTTPError as e:
            logging.warning(f"error getting repository permissions: {e}")
            return None

    @ats_cache()
    async def get_projects(self, session: Cloud):
        """Return a list of projects"""
        return await run_async(session.get, f"/2.0/workspaces/{self.team}/projects")

    @ats_cache()
    async def get_webhook_subscriptions(self, session: Cloud, repo_name: str):
        repo = await self.get_api_repository(session, repo_name)
        if repo is None:
            return None
        return await run_async(repo.get, "hooks")

    async def create_webhook_subscription_for_repo(
            self,
            session: Cloud,
            repo_name: str,
            url: str,
            active: bool,
            events: list[WebhookEvent],
            description: str,
    ):
        repo = await self.get_api_repository(session, repo_name)
        if repo is None:
            return None

        return await run_async(
            repo.post,
            path="hooks",
            json={
                "url": url,
                "active": active,
                "events": events,
                "description": description,
            },
        )

    async def delete_webhook_subscription(self, session: Cloud, repo_name, subscription_id) -> None:
        repo = await self.get_api_repository(session, repo_name)
        if repo is None:
            return None

        await run_async(
            repo.request,
            method="DELETE",
            path=f"hooks/{subscription_id}",
        )

    async def delete_repository(self, session: Cloud, repo_name: str):
        return await super().delete_repository(session, repo_name)

    async def get_session_author(self, session: Cloud) -> str:
        stored_session = await self.get_stored_session(None, session)
        author = ""
        if stored_session is not None:
            author = stored_session.credentials.author

        return author


register_plugin(PLUGIN_NAME, BitbucketCloud())
