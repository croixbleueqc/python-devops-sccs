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

import hashlib

class Demo(Sccs):
    def init(self, args):
        self.FAKE_DATA = {
            "test": {
                "REPO_TEST_01": "READ",
                "REPO_TEST_02": "ADMIN"
            },
            "test2": {
                "REPO_TEST2_01": "WRITE"
            }
        }

    def get_session_id(self, args):
        return hashlib.sha256(str(args).encode()).hexdigest()

    async def open_session(self, session_id, args):
        return {
            "user": args["user"],
            "id": session_id
            }

    async def close_session(self, session, args):
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

def init_plugin():
    return "demo", Demo()
