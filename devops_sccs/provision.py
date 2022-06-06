"""Provision module

Main target is to be able to create a new repo
To clone the content of another one
To apply some modification
To commit on the new repo
To push the new code upstream
To provide details on how to configure it on client side

TODO: isolate execution of the init script (unsafe for now)
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


import pygit2
import shutil
import re
import subprocess

import string
import random
import os
import logging

from .errors import AnswerRequired, AnswerValidatorFailure, AuthorSyntax

from .utils.aioify import aioify, getCoreAioify, cleanupCoreAiofy


class Provision(object):
    POOL = "provision"

    def __init__(self, checkout_base_path, main, repository, templates, max_workers=10):
        self.checkout_base_path = checkout_base_path

        self.main_contract = main
        self.repository_contract = repository
        self.templates = templates
        self.templates_contract_cache = self.generate_contract_templates()

        getCoreAioify().create_thread_pool(self.POOL, max_workers=max_workers)

    def cleanup(self):
        cleanupCoreAiofy(self.POOL)

    def create_git_credential(self, user, pub, key, author):
        return GitCredential(user, pub, key, author)

    def generate_contract_templates(self):
        """Generate the contract part from a template (template.setup.args)"""
        ui_templates = {}

        for name, template in self.templates.items():
            ui = {}

            # Setup part
            if (
                template.get("setup") is not None
                and template["setup"].get("args") is not None
            ):
                for arg, cfg in template["setup"]["args"].items():
                    ui[arg] = cfg.copy()
                    del ui[arg]["arg"]

            ui_templates[name] = ui

        return ui_templates

    def get_templates(self):
        return self.templates

    def get_add_repository_contract(self):
        """Provide all contracts to add a new repository"""
        return {
            "main": self.main_contract,
            "repository": self.repository_contract,
            "templates": self.templates_contract_cache,
        }

    def prepare_provision(self, repository, template, template_params):
        """Verify and prepare the repository provisioning

        Most of the work is to validate that we have valid answers to fulfil the contract.
        Please read the README for more details about what a contract and answers look like.

        Args:
            repository (dict): Answers to a repository contract
            template (str): Template to use
            template_params (dict): Answers to a template contract
        """
        # Verify repository (name, others)
        repository_name = repository.get("name")
        if repository_name is None:
            raise AnswerRequired("repository name")

        validator = self.main_contract["repository_validator"]
        g = re.match(validator, repository_name)
        if g is None:
            raise AnswerValidatorFailure("repository name", validator)

        self.validate(self.repository_contract, repository)

        # Verify template (required or not and valid)
        if self.main_contract["template_required"] and (
            template is None or template == ""
        ):
            raise AnswerRequired("template")

        init_template_cmd = None

        if template is not None and template != "":
            if template not in self.templates.keys():
                raise AnswerValidatorFailure(
                    "template", "|".join(self.templates.keys())
                )

            # Verify template_params
            self.validate(self.templates_contract_cache[template], template_params)

            # Create custom command
            init_template_cmd = self._create_initialize_template_command(
                self.templates[template]["setup"], template_params, repository_name
            )

        # Create storage definition for this new repository
        storage_definition = {
            "repository": repository,
            "template": template,
            "template_params": template_params,
        }

        # return useful content to provision the new repository
        return repository_name, storage_definition, init_template_cmd

    def validate(self, contract, answers):
        """Validate answers regarding the contract

        Please read the README for more details about what a contract and answers look like.

        Example:

        contract = {
            "name": {
                "type": "string",
                "description": "Project Name",
                "required": true,
                "default": null,
                "validator": "^[a-z][a-z,-,0-9]*[a-z]$",
                "arg": "--name={}"
            },
            "desc": {
                "type": "string",
                "description": "Description",
                "required": true,
                "default": null,
                "validator": ".+",
                "arg": "--desc='{}'"
            },
            "helloworld": {
                "type": "bool",
                "description": "Remove helloworld",
                "default": true,
                "arg": {
                    "true": "-c",
                    "false": null
                }
            }
        }

        answers = {
            "name": "test",
            "helloworld": True,
            "desc": "This is a test !"
        }


        Args:
            contract (dict): The contract to fulfill
            answers (dict): Answers to the contract
        """
        for line, details in contract.items():
            value = answers.get(line)

            if value is None:
                if details.get("required", False):
                    raise AnswerRequired(line)
                elif details.get("default") is not None:
                    value = details["default"]
                else:
                    continue

            details_type = details["type"]
            validator = details.get("validator")

            if validator:
                g = re.match(validator, value)
                if g is None:
                    raise AnswerValidatorFailure(line, validator)

            if details_type == "suggestion":
                if value not in details["values"]:
                    raise ValueError(f"{value} is unavailable in the suggestion list")
            elif details_type == "bool":
                if not isinstance(value, bool) and not isinstance(value, str):
                    raise TypeError(f"{line} is not a boolean value.")

    def _create_initialize_template_command(self, setup, answers, repository_name):
        """Create a command based on answers

        Internal function: There is no answers validation as this is expected to be done during prepare_add_repository

        Args example:

        setup = {
            "cmd": [
                "python",
                "setup.py",
                "init"
            ],
            "args": {
                "name": {
                    "type": "string",
                    "description": "Project Name",
                    "required": true,
                    "default": null,
                    "validator": "^[a-z][a-z,-]*[a-z]$",
                    "arg": "--name={}"
                },
                "desc": {
                    "type": "string",
                    "description": "Description",
                    "required": true,
                    "default": null,
                    "validator": ".+",
                    "arg": "--desc='{}'"
                },
                "helloworld": {
                    "type": "bool",
                    "description": "Remove helloworld",
                    "default": true,
                    "arg": {
                        "true": "-c",
                        "false": null
                    }
                }
            }
        }

        answers = {
            "name": "test",
            "helloworld": True,
            "desc": "This is a test !"
        }
        """
        # Create the main command part.
        # We are trying to substitute repository_name that is the only variable supported for now
        cmd = []
        for i in setup.get("cmd", []):
            cmd.append(i.format(repository_name=repository_name))

        if len(cmd) == 0:
            return None

        for arg, cfg in setup.get("args", {}).items():
            value = answers.get(arg)

            if value is None:
                if cfg.get("default") is not None:
                    value = cfg["default"]
                else:
                    continue

            cfg_type = cfg["type"]

            if cfg_type in ("string", "suggestion"):
                cmd.append(cfg["arg"].format(value))
            elif cfg_type == "bool":
                if isinstance(value, bool):
                    new_value = "true" if value else "false"
                elif isinstance(value, str):
                    new_value = value.lower()
                else:
                    raise TypeError(f"Argument {arg} is not a boolean value.")

                if cfg["arg"].get(new_value) is not None:
                    cmd.append(cfg["arg"][new_value])

        return cmd

    @aioify(pool=POOL)
    def provision(
        self,
        destination,
        destination_main_branch,
        additional_branches_mapping,
        template,
        initialize_template_command,
        git_credential,
        author=None,
        commit_message="Scaffold initialized !",
    ):
        """Provision a newly created repository in your sccs

        Workflow:
        - Initialize git credential (git_credential)
        - Clone the new repository (destination)
        - Add the template involved to provision the destination
        - Merge the main template branch to the main destination branch
        - Init the template with a shell command provided by initialize_template_command (if required)
        - Create additional branches based on additional_branches_mapping (if required)
        - Push everything to the destination
        - Clean up

        Args:
            destination (str): git url of the destination repository
            destination_main_branch (str): the destination main branch
            additional_branches_mapping (list(str)): Mapping list between template branches and destination branches (eg: [("deploy/dev", "deploy/dev"), ("deploy/dev", "deploy/prod")]
            template (str): Template to use
            initialize_template_command (list(str)): Shell command to initialize the template on the destination
            git_credential (GitCredential): Credential to connect on the template and destination
            author (str): "User <user@domain.tld>" of the real requester
            commit_message (str): Commit message used when initialize_template_command required

        Returns:
            str: How to use the new repository instructions
        """
        logging.info(f"{destination}: provisioning")

        # User how_to (common steps)
        user_clone = destination
        user_path = user_clone[user_clone.rfind("/") + 1 : len(user_clone) - 4]
        use_me = f"git clone {user_clone}\n"
        use_me += f"cd {user_path}\n"

        # Template required
        if template == "" or template is None:
            logging.info(f"{destination}: No template selected")
            return use_me

        # Extract information from selected template
        template_from_url = self.templates[template]["from"]["git"]
        template_from_main_branch = self.templates[template]["from"]["main_branch"]
        template_from_other_branches = self.templates[template]["from"].get(
            "other_branches", []
        )

        checkout_path = os.path.join(
            self.checkout_base_path, "".join(random.choices(string.ascii_letters, k=32))
        )

        # Git part
        # see: https://github.com/MichaelBoselowitz/pygit2-examples/blob/master/examples.py

        # Git Credential with callbacks
        callbacks = pygit2.RemoteCallbacks(credentials=git_credential.for_pygit2())

        # Git clone
        logging.debug(f"{destination}: cloning")
        intermediate = pygit2.clone_repository(
            destination, checkout_path, callbacks=callbacks
        )

        # Git add template repo
        logging.debug(f"{destination}: adding template {template_from_url}")
        remote_template = intermediate.remotes.create("template", template_from_url)
        remote_template.fetch(callbacks=callbacks)
        tpl_oid = intermediate.lookup_reference(
            f"refs/remotes/template/{template_from_main_branch}"
        ).target

        # Git create branch based on template and checkout
        logging.debug(
            f"{destination}: creating main branch '{destination_main_branch}'"
        )
        intermediate.create_branch(destination_main_branch, intermediate.get(tpl_oid))
        intermediate.checkout(f"refs/heads/{destination_main_branch}")

        # Execute custom script
        if initialize_template_command is not None:
            logging.debug(
                f"{destination}: running cmd: {' '.join(initialize_template_command)}"
            )
            # TODO: Unsafe for now ! Needs chroot/proot to isolate execution from others
            # TODO: do not raise an exception to give us the chance to clean up all around first

            proc = subprocess.run(initialize_template_command, cwd=checkout_path)
            proc.check_returncode()

            # Create commit
            logging.debug(f"{destination}: creating commit")

            committer_signature = GitCredential.create_pygit2_signature(
                git_credential.author
            )
            if author is None:
                author_signature = committer_signature
            else:
                author_signature = GitCredential.create_pygit2_signature(author)

            intermediate.index.add_all()
            intermediate.index.write()
            tree = intermediate.index.write_tree()
            _ = intermediate.create_commit(
                "HEAD",
                author_signature,
                committer_signature,
                commit_message,
                tree,
                [intermediate.head.target],
            )

        # additional branches
        logging.debug(f"{destination}: adding additional branches")

        additional_branches = []
        for template_branch, destination_branch in additional_branches_mapping:
            if destination_branch == destination_main_branch:
                logging.warning(
                    f"{destination}: override {destination_main_branch} is not allowed"
                )
                continue
            if template_branch not in template_from_other_branches:
                logging.warning(
                    f"{destination}: branch {template_branch} is not available for {template}"
                )
                continue

            tpl_oid = intermediate.lookup_reference(
                f"refs/remotes/template/{template_branch}"
            ).target
            intermediate.create_branch(destination_branch, intermediate.get(tpl_oid))
            additional_branches.append(destination_branch)

        # Git push to origin
        logging.debug(f"{destination}: pushing all branches to origin")

        remote_origin = None
        for repo in intermediate.remotes:
            if repo.name == "origin":
                remote_origin = repo
                break

        remote_origin.push([f"refs/heads/{destination_main_branch}"], callbacks)
        for additional_branch in additional_branches:
            remote_origin.push([f"refs/heads/{additional_branch}"], callbacks)

        # Clean up
        logging.debug(f"{destination}: cleaning")
        shutil.rmtree(checkout_path)

        # Return user how-to
        use_me += f"git remote add template {template_from_url}\n"
        use_me += "git fetch template"

        logging.info(f"{destination}: provision is done")
        return use_me


class GitCredential(object):
    """Credential for git

    Only support SSH key for now

    Args:
        user (str): Sccs username
        pub (str): Absolute path to the ssh public key
        key (str): Absolute path to the ssh private key
        author (str): Git author like "User <user@domain.tld>"
    """

    def __init__(self, user, pub, key, author):
        self.user = user
        self.pub = pub
        self.key = key
        self.author = author

    @classmethod
    def create_pygit2_signature(cls, author):
        """Create a signature based on git author syntax "User <user@domain.tld>"""

        # TODO: improve with a regex
        user_email = author.split("<")

        if len(user_email) != 2:
            raise AuthorSyntax(author)

        user = user_email[0].strip()
        email = user_email[1].replace(">", "").strip()
        return pygit2.Signature(user, email)

    def for_pygit2(self):
        """Use SSH key to connect with git"""
        return pygit2.Keypair(self.user, self.pub, self.key, "")
