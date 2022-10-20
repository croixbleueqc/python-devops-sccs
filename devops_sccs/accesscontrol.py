"""
Access Control module
"""

# Copyright 2021 Croix Bleue du Québec

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

from enum import IntEnum

from .errors import SccsException


class Action(IntEnum):
    GET_CONTINOUS_DEPLOYMENT_CONFIG = 0
    WATCH_CONTINOUS_DEPLOYMENT_CONFIG = 1
    GET_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE = 2
    WATCH_CONTINUOUS_DEPLOYMENT_VERSIONS_AVAILABLE = 3
    GET_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE = 4
    WATCH_CONTINUOUS_DEPLOYMENT_ENVIRONMENTS_AVAILABLE = 5
    GET_REPOSITORIES = 6
    WATCH_REPOSITORIES = 7


class Permission(object):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"

    READ_CAPABILITIES = [READ, WRITE, ADMIN]
    WRITE_CAPABILITIES = [WRITE, ADMIN]


class AccessForbidden(SccsException):
    def __init__(self, repository, action):
        super().__init__(f"Access forbidden for {repository} with action {action.name}")
