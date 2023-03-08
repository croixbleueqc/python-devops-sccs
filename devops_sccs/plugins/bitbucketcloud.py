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
from datetime import timedelta

from atlassian.bitbucket import Cloud
from atlassian.bitbucket.cloud.repositories import Repository
from atlassian.bitbucket.cloud.repositories.pipelines import Pipeline
from atlassian.bitbucket.cloud.repositories.refs import Branch
from atlassian.bitbucket.cloud.workspaces import Projects, Workspace
from atlassian.rest_client import AtlassianRestAPI
from requests import HTTPError

from devops_console.schemas import WebhookEvent
from devops_sccs.schemas.config import EnvironmentConfiguration, PluginConfig
from .cache_keys import cache_key_fns
from ..accesscontrol import Action, Permission
from ..client import register_plugin, SccsClient
from ..errors import SccsException, TriggerCdEnvUnsupported
from ..plugin import SccsApi, StoredSession
from ..provision import Provision
from ..redis import cache_async
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
                author=f"Admin User <{config.watcher.email}>",
                apikey=config.watcher.pwd, )
            self.admin_session = Cloud(
                username=self.watcher_user.user, password=self.watcher_user.apikey, cloud=True, )
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
            return hash((self.admin_session.username, self.admin_session.password))
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
            session=Cloud(
                username=credentials.user, password=credentials.apikey, cloud=True
                ) if credentials is not None else self.admin_session,
            credentials=credentials if credentials is not None else self.watcher_user, )

        self.local_sessions[session_id] = stored

        return stored

    async def close_session(
            self, session_id: int, ):
        session = await self.get_stored_session(session_id)
        if session is not None:
            try:
                session.shared_sessions -= 1

                if session.shared_sessions <= 0:
                    # not used anymore
                    self.local_sessions.pop(session_id)
            except Exception as e:
                logging.error(f"Error while closing session: {e}")
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

    @cache_async(ttl=timedelta(weeks=1))
    async def accesscontrol(self, session: Cloud, repo_slug: str, action: int = 0):
        """see plugin.py"""
        # will raise an HTTPError if access is forbidden
        try:
            await run_async(
                session.get,
                "user/permissions/repositories",
                params=[("q", f'repository.name="{repo_slug}"')], )  # type: ignore
        except HTTPError as e:
            logging.error(f"Access denied: {e}")
            raise

    async def passthrough(self, session: Cloud, request):
        return await super().passthrough(session, request)

    @cache_async(ttl=timedelta(days=1), key="repositories")
    async def get_repositories(
            self, session: Cloud | None
            ) -> list[typing_repo.Repository]:
        """see plugin.py"""
        if session is None:
            session = self.admin_session

        def get_repos_sync():
            result: list[typing_repo.Repository] = []
            for repository_permission in session._get_paged(
                    "user/permissions/repositories", params={"pagelen": 100}
                    ):
                assert isinstance(repository_permission, dict)
                repository = repository_permission["repository"]
                full_name = repository["full_name"]

                result.append(
                    typing_repo.Repository(
                        key=hash(repository_permission["repository"]["name"]),
                        name=repository["name"],
                        slug=full_name.split("/")[1],
                        url=repository["links"]["html"]["href"],
                        permission=repository_permission["permission"],
                        )
                    )
            return result

        return await run_async(get_repos_sync)

    @cache_async(ttl=timedelta(days=1))
    async def api_workspace(self, session: Cloud) -> Workspace:
        return await run_async(session.workspaces.get, self.team)

    @cache_async(ttl=timedelta(days=1))
    async def get_repository(
            self,
            session: Cloud,
            repo_slug: str,
            by="slug"
            ) -> typing_repo.Repository | None:
        """see plugin.py"""
        repos = await self.get_repositories(session)
        for repo in repos:
            if by == "slug" and repo.slug == repo_slug:
                return repo
            elif by == "name" and repo.name == repo_slug:
                return repo
        return None

    async def get_api_repository(self, session: Cloud, repo_slug: str) -> Repository | None:
        """Returns an unmodified Repository object as returned by the API"""
        await self.accesscontrol(session, repo_slug)

        workspace = await self.api_workspace(session)

        return await run_async(workspace.repositories.get, repo_slug)

    async def add_repository(
            self,
            session: Cloud,
            provision: Provision,
            repo_definition: dict,
            template: str,
            template_params: dict
            ):
        return await run_async(
            super().add_repository,
            session,
            provision,
            repo_definition,
            template,
            template_params
            )

    @cache_async(
        ttl=timedelta(days=1),
        key=cache_key_fns["get_continuous_deployment_config"],
        )
    async def get_continuous_deployment_config(
            self, session: Cloud | None, repo_slug: str, environments=None, ) -> list[
        typing_cd.EnvironmentConfig]:
        """
        fetch the version deployed in each environment
        """
        if environments is None:
            environments = []

        session = self.admin_session

        repo = await self.get_api_repository(session, repo_slug)
        if repo is None:
            return []

        def get_deploys_sync():
            deploys = []
            # Get supported branches
            for idx, branch_name in enumerate(self.cd_branches_accepted):
                try:
                    if len(environments) == 0 or self.cd_environments[idx].name in environments:
                        b = repo.branches.get(branch_name)
                        deploys.append((b, idx))
                except (KeyError, ValueError, HTTPError):
                    pass

            return sorted(deploys, key=lambda d: d[1])

        deploys = await run_async(get_deploys_sync)

        if len(deploys) == 0:
            logging.warning(f"Continuous deployment not supported for {repo_slug}")
            return []

        results = []

        for branch, index in deploys:
            cfg = await self.get_continuous_deployment_config_by_branch(
                repo,
                branch,
                self.cd_environments[index]
                )
            results.append(cfg[1])

        return results

    @cache_async(ttl=timedelta(days=1))
    async def get_continuous_deployment_versions_available(
            self,
            repo_slug: str
            ) -> list[typing_cd.Available]:
        """
        Get the list of version available to deploy
        """
        session = self.admin_session

        versions: list[typing_cd.Available] = []

        repo = await self.get_api_repository(session, repo_slug)
        if repo is None:
            return versions

        # noinspection PyProtectedMember
        def pl_gen():
            # NB: there is a `each()` method, but it doesn't have a pagelen parameter; so we have to do it manually
            for pl in repo.pipelines._get_paged(
                    None, trailing=True, paging_workaround=True, params={
                        "q": "target.ref_name=master", "sort": "-created_on", "pagelen": 100
                        }, ):
                yield Pipeline(
                    AtlassianRestAPI.url_joiner(repo.pipelines.url, pl["uuid"]),
                    pl,
                    **repo.pipelines._new_session_args
                    )

        def get_versions_sync():
            for pipeline in pl_gen():
                target = pipeline.get_data("target")
                state = pipeline.get_data("state")
                if target is None or state is None or target["type"] != "pipeline_ref_target":
                    continue
                ref_name = target["ref_name"]
                try:
                    result_name = state["result"]["name"]
                except KeyError:
                    logging.warning(f"Unexpected pipeline state: {state['name']}")
                    # TODO handle this error. Typically we see this exception when state["name"] ==
                    # "IN_PROGESS" which could be useful information to pass along to the caller
                    # although it falls outside the mandate of the current function.
                    continue
                if ref_name in self.cd_versions_available and result_name == "SUCCESSFUL":
                    available = typing_cd.Available(
                        key=hash((repo_slug, pipeline.build_number)),
                        build=str(pipeline.build_number),
                        version=target["commit"]["hash"], )
                    versions.append(available)

            return versions

        return await run_async(get_versions_sync)

    @cache_async(ttl=timedelta(days=1))
    async def get_continuous_deployment_environments_available(
            self, session: Cloud | None, repo_slug: str
            ) -> list[typing_cd.EnvironmentConfig]:
        session = self.admin_session

        envs: list[typing_cd.EnvironmentConfig] = []

        repo = await self.get_api_repository(session, repo_slug)
        if repo is None:
            return envs

        for environment in self.cd_environments:
            try:
                branch = await run_async(repo.branches.get, environment.branch)
                (_, cfg) = await self.get_continuous_deployment_config_by_branch(
                    repo,
                    branch,
                    environment
                    )
            except Exception:
                continue
            envs.append(cfg)

        return envs

    async def trigger_continuous_deployment(
            self, session: Cloud, repo_slug: str, environment: str, version: str
            ) -> typing_cd.EnvironmentConfig:
        """
        Trigger a deployment in a specific environment
        """
        await self.accesscontrol(session, repo_slug)

        logging.info(f"TRIGGER CD of {version} in {environment} for {repo_slug}")

        # Get Continuous Deployment configuration for the environment requested
        cd_environment_config: EnvironmentConfiguration
        for cd_environment in self.cd_environments:
            if cd_environment.name == environment:
                cd_environment_config = cd_environment
                break
        else:
            utils_cd.trigger_not_supported(repo_slug, environment)  # raises an exception

        # using user session for repo manipulations
        # Check current configuration using the cache. This is ok because the user will see the
        # deployed version anyway
        continuous_deployment: typing_cd.EnvironmentConfig
        try:
            continuous_deployment = (await self.get_continuous_deployment_config(
                self.admin_session, repo_slug, [environment], fetch=True
                ))[0]
        except IndexError:
            logging.warning(
                f"Continuous deployment config not found for {repo_slug} on environment {environment}"
                )
            raise TriggerCdEnvUnsupported(repo_slug, environment)

        versions_available = await self.get_continuous_deployment_versions_available(
            repo_slug, fetch=True
            )

        utils_cd.trigger_prepare(
            continuous_deployment, versions_available, repo_slug, environment, version
            )

        repo = await self.get_api_repository(session, repo_slug)
        if repo is None:
            raise TriggerCdEnvUnsupported(repo_slug, environment)

        # noinspection PyUnboundLocalVariable
        branch_name = cd_environment_config.branch  # code is unreachable if cd_environment_config is None (see above)

        deploy_branch_name = f"continuous-deployment-{environment}"

        if cd_environment_config.trigger.get("pullrequest", False):
            # Continuous Deployment is done with a PR.
            # We need to check if there is already one open (the version requested doesn't matter)
            def check_pr_sync():
                for pullrequest in repo.pullrequests.each():
                    if (
                            pullrequest.destination_branch == branch_name and pullrequest.title is not None and self.cd_pullrequest_tag in pullrequest.title):
                        raise SccsException(
                            f'A continuous deployment request is already open. link: {pullrequest.get_link("html")}'
                            )

            await run_async(check_pr_sync)

            deploy_branch = await run_async(repo.branches.get, branch_name)

            try:
                # If the branch already exists, we should remove it.
                await run_async(repo.branches.delete, path=f"{deploy_branch_name}")
            except HTTPError:
                pass
            await run_async(repo.branches.create, deploy_branch_name, deploy_branch.hash)
        else:
            deploy_branch = None

        # requests.request(
        #     "POST", repo.url + "/src", data={
        #         f'/{cd_environment_config.version["file"]}': f"{version}\n",
        #         "message": f"deploy version {version}",
        #         "author": author,
        #         "branch": branch_name if deploy_branch is None else deploy_branch_name,
        #         }, auth=(session.username, session.password),  # type: ignore
        #     )
        post_branch_name = branch_name if deploy_branch is None else deploy_branch_name
        logging.info(
            f"TRIGGER CD: POST {version} to version.txt for {repo_slug} on {post_branch_name}"
            )
        await run_async(
            repo.post,
            "src",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                f'/{cd_environment_config.version["file"]}': f"{version}\n",
                "message": f"deploy version {version}",
                "author": (await self.get_session_author(session)),
                "branch": post_branch_name,
                },
            files=[],
            # see https://github.com/atlassian-api/atlassian-python-api/issues/1045#issue-1368184335
            )

        if deploy_branch is not None:
            # Continuous Deployment is done with a PR.
            pr = await run_async(
                repo.pullrequests.create,
                title=f"Upgrade {environment} {self.cd_pullrequest_tag}",
                source_branch=deploy_branch_name,
                destination_branch=branch_name,
                close_source_branch=True
                )

            # race condition start here
            continuous_deployment.pullrequest = pr.get_link("html")
        else:
            # Continuous Deployment done
            continuous_deployment.version = version

        # Return the new configuration (new version or PR in progress)
        return continuous_deployment

    async def bridge_repository_to_namespace(
            self, session: Cloud, repo_slug: str, environment: str, untrustable: bool
            ):
        return await super().bridge_repository_to_namespace(
            session, repo_slug, environment, untrustable
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
            author: str,
            date: str,
            config: EnvironmentConfiguration,
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
            author=author,
            date=date,
            readonly=not trigger_config.get("enabled", True),
            pullrequest=pullrequest if trigger_config.get("pullrequest", False) else None, )
        return env

    async def get_continuous_deployment_config_by_branch(
            self, repo: Repository, branch: Branch, config: EnvironmentConfiguration
            ) -> tuple[str, typing_cd.EnvironmentConfig]:
        """
        Get environment configuration for a specific branch
        """
        # Get version
        version_file = config.version.get("file")
        commit_hash = branch.hash
        version: str
        if version_file is not None:
            res: bytes | None = await run_async(
                repo.get,
                path=f"src/{commit_hash}/{version_file}",
                not_json_response=True
                )  # type: ignore
            if res is not None:
                version = res.decode("utf-8").strip()
            else:
                raise SccsException(
                    f"failed to get version from {version_file} for {repo.name} on branch {branch}"
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
                            pullrequest.destination_branch == config.branch and self.cd_pullrequest_tag in (
                            pullrequest.title or "")):
                        link = pullrequest.get_link("html")
                        pullrequest_link = link["href"] if type(link) is dict else link
                        break

            await run_async(pr_sync)

        try:
            author = branch.data["target"]["author"]["user"]["display_name"]
        except KeyError:
            author = branch.data["target"]["author"]["raw"]

        date = branch.data["target"]["date"]

        return (branch.name, BitbucketCloud.create_continuous_deployment_config_by_branch(
            repo.slug, version, branch.name, author, date, config, pullrequest_link
            ))

    @cache_async(ttl=timedelta(days=1))
    async def get_repository_permission(self, session: Cloud, repo_slug: str) -> str | None:
        # get repository permissions for user

        try:
            user_permissions = await run_async(
                session.get,
                f"repositories/{self.team}/{repo_slug}/permissions-config/users/{session.username}"
                )
            return user_permissions.get("permission")
        except HTTPError as e:
            logging.warning(f"Error getting repository permissions: {e}")
            return None

    @cache_async(ttl=timedelta(days=1))
    async def get_projects(self, session: Cloud) -> Projects:
        """Return a list of projects"""
        return await run_async(session.get, f"/workspaces/{self.team}/projects")

    @cache_async(ttl=timedelta(days=1))
    async def get_webhook_subscriptions(self, session: Cloud, repo_slug: str):
        repo = await self.get_api_repository(session, repo_slug)
        if repo is None:
            return None
        return await run_async(repo.get, "hooks")

    async def create_webhook_subscription_for_repo(
            self,
            session: Cloud,
            repo_slug: str,
            url: str,
            active: bool,
            events: list[WebhookEvent],
            description: str, ):
        repo = await self.get_api_repository(session, repo_slug)
        if repo is None:
            return None

        return await run_async(
            repo.post, path="hooks", json={
                "url": url, "active": active, "events": events, "description": description,
                }
            )

    async def delete_webhook_subscription(self, session: Cloud, repo_slug, subscription_id) -> None:
        repo = await self.get_api_repository(session, repo_slug)
        if repo is None:
            return None

        await run_async(
            repo.request, method="DELETE", path=f"hooks/{subscription_id}", )

    async def delete_repository(self, session: Cloud, repo_slug: str):
        return await super().delete_repository(session, repo_slug)

    async def get_session_author(self, session: Cloud) -> str:
        stored_session = await self.get_stored_session(None, session)
        if stored_session is not None:
            return stored_session.credentials.author
        elif session.username == self.watcher_user.user:
            return self.watcher_user.author

        return ""


register_plugin(PLUGIN_NAME, BitbucketCloud())
