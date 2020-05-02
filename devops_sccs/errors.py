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

class SccsException(Exception):
    pass

class PluginAlreadyRegistered(SccsException):
    def __init__(self, plugin_id):
        super().__init__(f"{plugin_id} already exist. Please verify that another plugin do not overlap it.")

class PluginNotRegistered(SccsException):
    def __init__(self, plugin_id):
        super().__init__(f"Plugin {plugin_id} not registered !")

class AnswerRequired(SccsException):
    def __init__(self, arg):
        super().__init__(f"Argument {arg} is required.")

class AnswerValidatorFailure(SccsException):
    def __init__(self, arg, validator):
        super().__init__(f"Argument {arg} failed to be validate with the regex {validator}")