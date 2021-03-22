"""
Demo plugin

will fake operations to demonstrate what a plugin should do.
"""

# Copyright 2019 mickybart
# Copyright 2020 Croix Bleue du Québec

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

from ..plugin import Sccs
from ..typing import cd as typing_cd
from ..utils import cd as utils_cd

import hashlib

class Demo(Sccs):
    async def init(self, core, args):
        # Mock few repositories with permissions
        self.FAKE_DATA = {
            "test": {
                "REPO_TEST_01": "READ",
                "REPO_TEST_02": "ADMIN"
            },
            "test2": {
                "REPO_TEST2_01": "WRITE"
            }
        }

        # Mock Continuous Deployments for few repositories
        self.FAKE_CD = {
            "REPO_TEST_01": {
                "availables": [
                    {
                        "build": "10",
                        "version": "0.5"
                    },
                    {
                        "build": "11",
                        "version": "1.0"
                    }
                ],
                "environments": [
                    {
                        "environment": "master",
                        "version": "1.0",
                        "readonly": True
                    },
                    {
                        "environment": "development",
                        "version": "0.5"
                    }
                ]
            },
            "REPO_TEST_02": {
                "availables": [
                    {
                        "build": "100",
                        "version": "2.0"
                    }
                ],
                "environments": [
                    {
                        "environment": "master",
                        "version": "2.0",
                        "readonly": True
                    },
                    {
                        "environment": "production",
                        "version": "1.8",
                        "readonly": True
                    }
                ]
            }
        }

    async def cleanup(self):
        pass

    def get_session_id(self, args):
        return hashlib.sha256(str(args).encode()).hexdigest()

    async def open_session(self, session_id, args):
        return {
            "user": args["user"],
            "id": session_id
            }

    async def close_session(self, session_id, session, args):
        pass

    async def get_repositories(self, session, args):
        user = session["user"]

        user_data = self.FAKE_DATA.get(user)

        if user_data is None:
            return []
        
        return list(user_data.keys())

    async def get_repository_permissions(self, session, repository, args):
        user = session["user"]

        user_data = self.FAKE_DATA.get(user)

        if user_data is None:
            return "UNKNOWN"

        return user_data.get(repository, "UNKNOWN")

    async def get_all_repositories_permissions(self, session, args):
        user = session["user"]
        user_data = self.FAKE_DATA.get(user)

        if user_data is None:
            return []
        
        return user_data
    
    async def passthrough(self, session, request, args):
        if request == "echo":
            return f"Proprietary {request} request with args: {args}"
        else:
            return f"Proprietary {request} NOT supported !"

    async def get_continuous_deployment_config(self, session, repository, args):
        user = session["user"]
        repo_data = self.FAKE_CD.get(repository)

        if repo_data is None:
            return None

        config = typing_cd.Config(repo_data)

        return config

    async def trigger_continuous_deployment(self, session, repository, environment, version, args):
        user = session["user"]
        config = await self.get_continuous_deployment_config(session, repository, args)

        if config is None:
            utils_cd.trigger_not_supported(repository)

        env_config, available_config = utils_cd.trigger_prepare(config, repository, environment, version)

        env_config.version = available_config.version

        self.FAKE_CD[repository] = config.dumps()

def init_plugin():
    return "demo", Demo()
