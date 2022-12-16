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
        super().__init__(
            f"{plugin_id} already exist. Please verify that another plugin do not overlap it."
            )


class PluginNotRegistered(SccsException):
    def __init__(self, plugin_id):
        super().__init__(f"Plugin {plugin_id} not registered !")


class AnswerRequired(SccsException):
    def __init__(self, arg):
        super().__init__(f"Argument {arg} is required.")


class AnswerValidatorFailure(SccsException):
    def __init__(self, arg, validator):
        super().__init__(f"Argument {arg} failed to be validate with the regex {validator}")


class TriggerCdReadOnly(SccsException):
    def __init__(self, repository, environment):
        super().__init__(f"{environment} for {repository} is readonly !")


class TriggerCdNotSupported(SccsException):
    def __init__(self, repository):
        super().__init__(f"Continuous Deployment is not supported for {repository}")


class TriggerCdEnvUnsupported(SccsException):
    def __init__(self, repo_slug, environment):
        super().__init__(f"Environment {environment} is not supported for {repo_slug}")


class TriggerCdVersionUnsupported(SccsException):
    def __init__(self, repository, version):
        super().__init__(f"Version {version} is not supported for {repository}")


class TriggerCdVersionAlreadyDeployed(SccsException):
    def __init__(self, repository, environment, version):
        super().__init__(f"Version {version} is already deployed in {environment} for {repository}")


class AuthorSyntax(SccsException):
    def __init__(self, author):
        super().__init__(
            f"Author {author} is not compliant with the format 'user <user@domain.tld>'"
            )


class AccessForbidden(SccsException):
    pass
