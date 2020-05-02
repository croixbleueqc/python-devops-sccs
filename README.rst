DevOps Sccs (Asyncio)
=====================

Python library to abstract different Source code control systems (sccs) with asyncio.

This library can be enriched by plugins and support common APIs across sccs and proprietary APIs if needed.

The main purpose is to build a backend or a frontend independant of any sccs.
By doing so, it will be possible to switch the sccs backend or to use more than one wihout any changes on the frontend/backend layers.

Installation
------------

This package is available for Python 3.5+.

.. code:: bash

    pip3 install --user .

Configuration
-------------

Configuration can be done via a dict object. The configuration is passed to the library to initialize all components and plugins.

Examples below can be adapted and used in a config.json file.

Please removes any comments as this is not supported by json format.

Default configuration
^^^^^^^^^^^^^^^^^^^^^

You can omit the config parameters during initialization of the library. This is equivalent to use:

.. code:: bash

    {}

Plugins configuration
^^^^^^^^^^^^^^^^^^^^^

.. code:: bash

    {
        "plugins" : {
            "external": "/path/to/external/plugins/",
            "builtin" : {
                "demo": true # true by default. set to false to disable the demo builtin plugin
            },
            "config": {
                "<plugin unique id (eg: bc)>" : {
                    <custom options for the plugin...>
                }
            }
        }
    }

Provisioning configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^

This is an early alpha feature not well documented for now. Please check code source for more details.

The main purpose is to provide a standard way to create new repositories, enable pipelines, fork code from scaffold with custom init support etc.

This config permit to generate templates and frontend contracts that need to be filled to provision a template.

.. code:: bash

    {
        "checkout_base_path": "/tmp/devops/provision",
        "templates": {
            "<scaffold's name>": {
                <options...>
            },
            "scaffold-aiohttp": {
                "from": {
                    "git": "git@bitbucket.org:<team>/scaffold-aiohttp.git",
                    "branch": "dev"
                },
                "setup": {
                    "cmd": [
                        "python",
                        "setup.py",
                        "init"
                    ],
                    "args": {
                        "name": {
                            "type": "string",
                            "desc": "Project Name",
                            "arg": "--name={}",
                            "required": true,
                            "default": null,
                            "validator": "^[a-z][a-z,-]*[a-z]$"
                        },
                        "desc": {
                            "type": "string",
                            "desc": "Description",
                            "arg": "--desc='{}'",
                            "required": true,
                            "default": null,
                            "validator": null
                        },
                        "helloworld": {
                            "type": "bool",
                            "desc": "Remove helloworld",
                            "arg": {
                                "true": "-c",
                                "false": null
                            },
                            "required": true,
                            "default": false,
                            "validator": null
                        }
                    }
                }
            }
        }
    }

Usage
-----

This library is based on asyncio so await/async constraints applied. To make code examples more readeable, we will omit some codes that are relevant to asyncio itself to execute a coroutine.

Initialize DevOps Sccs lib
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code:: python

    import asyncio
    import json
    
    from devops_sccs.core import Core

    async def main():
        with open("config.json", "r") as f:
            config = json.loads(f.read())

        core = Core(config)
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.close()

List plugins registered
^^^^^^^^^^^^^^^^^^^^^^^

.. code:: python

    print("Plugins registered:")
    print(core.plugins)

Commands available with a plugin
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

We are using the demo built-in plugin.

.. code:: python

    ctx_1 = await core.create_context("demo", {"user": "test"})

    # get repositories (unaccessible repositories to the ctx_1 will not be shown)
    print(await ctx_1.get_repositories())

    # get permissions for a specific repository
    repo = "REPO_TEST_02"
    print(
        "{}: {}".format(
            repo,
            await ctx_1.get_repository_permissions(repo)
            )
        )

    # Get all repositories permissions for the user
    print(await ctx_1.get_all_repositories_permissions())

    # Use the proprietary passthrough function to handle plugin proprietary APIs
    print(await ctx_1.passthrough("echo", { "todo": "demo!" }))

    print(await ctx_1.passthrough("do_you_understand_me_?"))

    await core.delete_context(ctx_1)

Provisioning
^^^^^^^^^^^^

This is an early alpha feature not well documented for now. Please check code source for more details.

.. code:: python

    print(core.provision.get_templates())
    print(core.provision.get_frontend_templates())

Write a plugin
--------------

First of all you can create an external plugin that will be packaged outside of this library.
The main purpose is to provide an easy way to extend DevOps Sccs without the need to fork it.

IMPORTANT: If your plugin is enough generic, be free to submit it in the core project.

NOTE: It is always possible to create an external plugin which inherit a built-in plugin. It is important to keep that in mind before building a new one from scratch.

Create an external folder
^^^^^^^^^^^^^^^^^^^^^^^^^

You need to create a folder wherever you want. Once done please configure the config.json file to set the plugins/external key.

Create the plugin
^^^^^^^^^^^^^^^^^

This plugin is not feature complete but is just a minimal example about how to create one with a real Sccs (bitbucket cloud).
You can still take a look at the demo built-in plugin.

Bitbucket library is used to communicate with Bitbucket Cloud.

As the bitbucket library is not compatible with asyncio, some helper functions (integrated in this lib) are used to make it compatible with asyncio.

.. code:: python

    import hashlib

    from devops_sccs.plugin import Sccs

    # helper to make sync code compatible with asyncio
    from devops_sccs.utils.aioify import getCoreAioify, aioify

    from pybitbucket.bitbucket import Client
    from pybitbucket.auth import BasicAuthenticator
    from pybitbucket.repository import Repository
    from pybitbucket.team import Team

    from uritemplate import expand

    def init_plugin():
        return "bc", BitbucketCloud()

    class BitbucketCloud(Sccs):
        POOL="bc"

        def init(self, args):
            getCoreAioify().create_thread_pool(
                self.POOL,
                max_workers=args.get("max_workers", 5) if args else 5
                )

        def get_session_id(self, args):
            return hashlib.sha256(str(args).encode()).hexdigest()

        async def open_session(self, session_id, args):
            return Client(
                BasicAuthenticator(
                    args["user"],
                    args["key"],
                    args["email"]
                )
            )

        async def close_session(self, session, args):
            pass

        @aioify(POOL)
        def get_repositories(self, session, args):
            repositories = []
            teams = [team.username for team in Team.find_teams_for_role(client=session, role="member")]

            for team in teams:
                for repo in Repository.find_repositories_by_owner_and_role(client=session, role="member", owner=team):
                    if isinstance(repo, Repository):
                        repositories.append(
                            {
                                "name": repo.name,
                                "owner": repo.owner.username,
                                "link": repo.links["html"]["href"]
                            }
                        )
            
            # TODO: note that the library do not yet enforce what a repo should look like (see plugin.py)
            return repositories

Use the new plugin
^^^^^^^^^^^^^^^^^^

.. code:: python

    ctx_bc_1 = await core.create_context("bc",
        {
            "user": '',
            "key": '',
            "email": ''
        }
    )
    print(await ctx_bc_1.get_repositories())

    await core.delete_context(ctx_bc_1)
