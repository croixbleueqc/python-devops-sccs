"""
Continuous Deployment Typing

Define standard typing to manage continuous deployment
"""

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

from . import WatcherType


class EnvironmentConfig(WatcherType):
    """
    Defines which version is deployed on a specific environment.

    readonly field permit to know if we can trigger a continuous deployment for this environment

    """

    environment: str
    version: str
    readonly: bool = False
    pullrequest: str | None = None
    author: str | None
    date: str | None

    def __eq__(self, other):
        if not isinstance(other, EnvironmentConfig):
            return False

        return (
                self.environment == other.environment
                and self.version == other.version
                and self.readonly == other.readonly
                and self.pullrequest == other.pullrequest
        )

    def __hash__(self):
        return hash((self.environment, self.version, self.readonly, self.pullrequest))


class Available(WatcherType):
    """
    Defines an available deployment (typically generated from a pipeline) with a build number and an associated version.
    """

    build: str
    version: str

    def __eq__(self, other):
        if not isinstance(other, Available):
            return False

        return self.build == other.build and self.version == other.version

    def __hash__(self):
        return hash((self.build, self.version))
