"""
Compliance Typing

Define standard typing to manage report, diverence, etc
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

from enum import Enum

from pydantic import BaseModel


class CurrentExpected(str, Enum):
    SET = "set"
    UNSET = "unset"
    MATCH = "match"
    UNMATCH = "unmatch"
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class Divergence(BaseModel):
    rule: str

    # current and expected are not limited to CurrentExpected class values
    current: CurrentExpected
    expected: CurrentExpected

    def __eq__(self, other):
        if not isinstance(other, Divergence):
            return False

        return (
                self.rule == other.rule
                and str(self.current) == str(other.current)
                and str(self.expected) == str(other.expected)
        )


class RepositoryDivergence(BaseModel):
    name: str
    divergences: list[Divergence] = []

    def pre_loads(self, data):
        # Change the structure to move the key as the name field
        preload_data = list(data.items())
        if len(preload_data) != 1:
            return data

        return {
            "name": preload_data[0][0],
            "divergences": preload_data[0][1]["divergences"],
            }

    def post_dumps(self, raw, dump):
        # Change the structure to move name as the key
        name = dump.pop("name")
        divergences = dump.pop("divergences")
        dump[name] = {"divergences": divergences}

    def isDivergences(self):
        return len(self.divergences) != 0

    def __eq__(self, other):
        if not isinstance(other, RepositoryDivergence):
            return False

        if self.name != other.name:
            return False

        if len(self.divergences) != len(other.divergences):
            return False

        for divergence in self.divergences:
            if divergence not in other.divergences:
                return False

        return True
