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

from .errors import AnswerRequired, AnswerValidatorFailure

from .utils.aioify import aioify, getCoreAioify

class Provision(object):
    POOL="provision"

    def __init__(self, checkout_base_path, templates={}, max_workers=5):
        self.templates = templates
        self.templates_frontend_cache = self.generate_frontend_templates()
        self.checkout_base_path = checkout_base_path

        getCoreAioify().create_thread_pool(self.POOL, max_workers=max_workers)

    def generate_frontend_templates(self):
        ui_templates = []

        for name, template in self.templates.items():
            # Template's name
            ui = {
                "desc": name
            }

            # Setup part
            if template.get("setup") is None or template["setup"].get("args") is None:
                ui["args"] = None
            else:
                args = {}
                for arg, cfg in template["setup"]["args"].items():
                    args[arg] = cfg.copy()
                    del args[arg]["arg"]
                
                ui["args"] = args

            # Extra part
            ui["extra"] = template.get("extra")

            ui_templates.append(ui)
        
        return ui_templates

    def get_templates(self):
        return self.templates

    def get_frontend_templates(self):
        return self.templates_frontend_cache

    def create_command(self, setup, answers):
        """ Create a command based on answers

        Answers eg:

        simulate_answers = {
            "template": "scaffold-aiohttp",
            "setup": {
                "answers": {
                    "name": "test",
                    "helloworld": True,
                    "desc": "This is a test !"
                }
            },
            "commit": {
                "branch": "qa"
            }
        }
        """
        cmd = setup["cmd"][:]

        for arg, cfg in setup["args"].items():
            value = answers.get(arg)

            if value is None:
                if cfg["required"]:
                    raise AnswerRequired(arg)
                elif cfg.get("default") is not None:
                    value = cfg["default"]
                else:
                    continue
            
            cfg_type = cfg["type"]
            validator = cfg.get("validator")

            if validator:
                g = re.match(validator, value)
                if g is None:
                    raise AnswerValidatorFailure(arg, validator)

            if cfg_type == "string":
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

    def is_payload_valid(self, payload):
        try:
            template = self.templates[payload["template"]]
            template_answers = payload["setup"]["answers"]
            self.create_command(template["setup"], template_answers)
        except:
            return False

        return True

    @aioify(pool=POOL)
    def execute(self, git_credential, destination, payload, commit_message="Scaffold initialized !"):
        # Extract data
        template = self.templates[payload["template"]]
        template_answers = payload["setup"]["answers"]
        commit_branch = payload["commit"]["branch"]

        template_from_url = template["from"]["git"]
        template_from_branch = template["from"]["branch"]
        
        checkout_path = os.path.join(
            self.checkout_base_path,
            ''.join(random.choices(string.ascii_letters, k=32))
        )
        
        # Git part
        # see: https://github.com/MichaelBoselowitz/pygit2-examples/blob/master/examples.py

        # Git Credential with callbacks
        callbacks = pygit2.RemoteCallbacks(
            credentials=git_credential.for_pygit2()
            )

        # Git clone
        intermediate = pygit2.clone_repository(destination, checkout_path, callbacks=callbacks)

        # Git add template repo
        remote_template = intermediate.remotes.create("template",template_from_url)
        remote_template.fetch(callbacks=callbacks)
        tpl_oid = intermediate.lookup_reference(f"refs/remotes/template/{template_from_branch}").target

        # Git create branch based on template and checkout
        intermediate.create_branch(commit_branch, intermediate.get(tpl_oid))
        intermediate.checkout(f"refs/heads/{commit_branch}")

        # Execute custom script
        cmd = self.create_command(template["setup"], template_answers)
        proc = subprocess.run(cmd, cwd=checkout_path)
        proc.check_returncode()

        # Create commit
        intermediate.index.add_all()
        intermediate.index.write()
        user = intermediate.default_signature
        tree = intermediate.index.write_tree()
        _ = intermediate.create_commit('HEAD', user, user, commit_message, tree, [intermediate.head.target])

        # Git push to origin

        remote_origin = None
        for repo in intermediate.remotes:
            if repo.name == "origin":
                remote_origin = repo
                break

        remote_origin.push([f"refs/heads/{commit_branch}"], callbacks)

        # Clean up
        shutil.rmtree(checkout_path)

        # Return user how-to
        user_clone = destination
        user_path = user_clone[user_clone.rfind("/")+1:len(user_clone)-4]

        use_me = f'git clone {user_clone}\n'
        use_me += f'cd {user_path}\n'
        use_me += f'git remote add template {template_from_url}\n'

        return {
            "guideline": use_me
        }

class GitCredential(object):
    def __init__(self, user, pub, key):
        self.user = user
        self.pub = pub
        self.key = key

    def for_pygit2(self):
        return pygit2.Keypair(self.user, self.pub, self.key, "")