# Copyright 2021-2022 Croix Bleue du Québec

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

from .accesscontrol import Action
from .plugin import SccsApi


class Context:
    """Context permits to communicate with a source code control system for a specific session.
    A session is an abstract concept that can hold nothing or any object type understandable
    by the plugin which issued it.
    """

    UUID_WATCH_CONTINOUS_DEPLOYMENT_CONFIG = "a7d7cba8-1a49-426c-9811-29022aca1a5a"
    UUID_WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE = "40e9a2d5-fc22-497e-a364-d19115321ba2"
    UUID_WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE = "7f7cd008-3350-47b7-80ce-9472e3a649c1"
    UUID_WATCH_REPOSITORIES = "865eb6e0-ded6-4cae-834b-603a22293086"

    def __init__(self, session_id, session, plugin: SccsApi, client):
        self.session_id = session_id
        self.session = session
        self.plugin = plugin
        self._client = client

    async def accesscontrol(self, repository, action):
        return await self.plugin.accesscontrol(self.session, repository, action)

    async def passthrough(self, request):
        return await self.plugin.passthrough(self.session, request)

    async def get_repositories(self):
        return await self.plugin.get_repositories(self.session)

    async def get_repository(self, repo_name):
        return await self.plugin.get_repository(self.session, repo_name)

    async def watch_repositories(self, poll_interval: int, *args, **kwargs):
        return self._client.scheduler.watch(
            (Context.UUID_WATCH_REPOSITORIES, self.session_id),
            poll_interval,
            self.plugin.get_repositories,
            session=self.session,
            *args,
            **kwargs,
        )

    async def get_continuous_deployment_config(self, repo_name, environments=[]):
        return await self.plugin.get_continuous_deployment_config(
            self.session, repo_name, environments
        )

    async def watch_continuous_deployment_config(
            self,
            repo_name: str,
            environments: list | None,
            poll_interval: int,
            *args,
            **kwargs,
    ):
        if environments is None:
            environments = []
        await self.accesscontrol(repo_name, Action.WATCH_CONTINOUS_DEPLOYMENT_CONFIG)

        def filtering_by_environment(event):
            return not environments or event.value.environment in environments

        return self._client.scheduler.watch(
            (Context.UUID_WATCH_CONTINOUS_DEPLOYMENT_CONFIG, repo_name),
            poll_interval,
            self.plugin.get_continuous_deployment_config,
            filtering=filtering_by_environment,
            session=None,  # Shared session, ie admin session
            repo_name=repo_name,
            environments=environments,
            bypass_func_cache=True,
            *args,
            **kwargs,
        )

    async def get_continuous_deployment_versions_available(self, repository):
        return await self.plugin.get_continuous_deployment_versions_available(
            self.session, repository
        )

    async def watch_continuous_deployment_versions_available(
            self, repo_name: str, poll_interval: int, *args, **kwargs
    ):
        await self.accesscontrol(repo_name, Action.WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE)

        return self._client.scheduler.watch(
            (Context.UUID_WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE, repo_name),
            poll_interval,
            self.plugin.get_continuous_deployment_versions_available,
            session=None,  # Shared session, ie admin session
            repo_name=repo_name,
            *args,
            **kwargs,
        )

    async def trigger_continuous_deployment(self, repository, environment, version):
        result = await self.plugin.trigger_continuous_deployment(
            self.session, repository, environment, version
        )

        self._client.scheduler.notify((Context.UUID_WATCH_CONTINOUS_DEPLOYMENT_CONFIG, repository))

        return result.dict()

    async def get_continuous_deployment_environments_available(self, repo_name):
        return await self.plugin.get_continuous_deployment_environments_available(
            self.session, repo_name
        )

    async def watch_continuous_deployment_environments_available(
            self, repo_name, poll_interval: int, *args, **kwargs
    ):
        await self.accesscontrol(
            repo_name, Action.WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE
        )

        return self._client.scheduler.watch(
            (
                Context.UUID_WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE,
                repo_name,
            ),
            poll_interval,
            self.plugin.get_continuous_deployment_environments_available,
            session=None,  # Shared session, ie admin session
            repo_name=repo_name,
            *args,
            **kwargs,
        )

    # TODO: remove this method (unused)
    async def bridge_repository_to_namespace(
            self, repo_name: str, environment: str, untrustable=True
    ):
        return await self.plugin.bridge_repository_to_namespace(
            self.session, repo_name, environment, untrustable
        )

    def get_add_repository_contract(self):
        return self._client.provision.get_add_repository_contract()

    async def add_repository(self, repository, template, template_params):
        result = await self.plugin.add_repository(
            self.session,
            self._client.provision,
            repository,
            template,
            template_params,
        )

        self._client.scheduler.notify((Context.UUID_WATCH_REPOSITORIES, self.session_id))

        return result

    async def delete_repository(self, repo_name):
        return await self.plugin.delete_repository(self.session, repo_name)

    async def compliance(self, remediation=False, report=False):
        return await self.plugin.compliance(self.session, remediation, report)

    async def compliance_report(self):
        return await self.plugin.compliance_report(self.session)

    async def compliance_repository(self, repo_name, remediation=False, report=False):
        return await self.plugin.compliance_repository(self.session, repo_name, remediation, report)

    async def compliance_report_repository(self, repo_name):
        return await self.plugin.compliance_report_repository(self.session, repo_name)

    async def get_webhook_subscriptions(self, repo_name: str):
        return await self.plugin.get_webhook_subscriptions(self.session, repo_name)

    async def create_webhook_subscription(self, repo_name, url, active, events, description):
        return await self.plugin.create_webhook_subscription_for_repo(
            self.session, repo_name, url, active, events, description
        )

    async def delete_webhook_subscription(self, repo_name, subscription_id):
        return await self.plugin.delete_webhook_subscription(
            self.session, repo_name, subscription_id
        )

    async def get_projects(self):
        return await self.plugin.get_projects(self.session)

    async def get_repository_permission(self, repo_name):
        return await self.plugin.get_repository_permission(self.session, repo_name)
