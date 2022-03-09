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

The main purpose is to provide a standard way to create new repositories, enable pipelines, fork code from scaffold with custom init support etc.

This config permit to generate templates and contracts that need to be fulfilled to add a new repository.

The next configuration is a possible example. Most part of this configuration is based on the contract/answers strategy (please read "Add a new repository" section).

.. code:: bash

    {
        "provision": {
            "checkout_base_path": "/tmp/devops/provision",
            "main": {
                "repository_validator": "^[a-z][a-z,-]*[a-z]$",
                "template_required": false
            },
            "repository": {
                "project": {
                    "type": "suggestion",
                    "description": "Project group",
                    "required": true,
                    "roleName": "name",
                    "values": [
                        {
                            "name": "DevOps",
                            "key": "DO"
                        },
                        {
                            "name": "Proof Of Concept",
                            "key": "POC"
                        }
                    ]
                },
                "configuration": {
                    "type": "suggestion",
                    "description": "Branches strategy",
                    "required": true,
                    "roleName": "short",
                    "values": [
                        {
                            "short": "master; depoy/*",
                            "key": "trunkbased-deploy"
                        },
                        {
                            "short": "master only",
                            "key": "trunkbased"
                        }
                    ]
                },
                "privileges": {
                    "type": "suggestion",
                    "description": "Privileges",
                    "required": true,
                    "roleName": "short",
                    "values": [
                        {
                            "short": "DevOps (Admin)",
                            "key": "devops-full-only"
                        },
                        {
                            "short": "Devs",
                            "key": "dev-default"
                        }
                    ]
                }
            },
            "templates": {
                "scaffold-aiohttp": {
                    "from": {
                        "git": "https://github.com/croixbleueqc/scaffold-aiohttp",
                        "main_branch": "master",
                        "other_branches": []
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

        core = await Core.create(config)
    
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

    # Get continuous deployment configuration
    print(await ctx_1.get_continuous_deployment_config("REPO_TEST_01"))

    # Trigger continuous deployment (+ print to see the effective change)
    await ctx_1.trigger_continuous_deployment("REPO_TEST_01", "development", "1.0")
    print(await ctx_1.get_continuous_deployment_config("REPO_TEST_01"))

    # List all environments that can be used to run the application
    print(await ctx_1.get_runnable_environments("REPO_TEST_01"))

    # Bridge repository/environment to a kubernetes namespace
    print(await ctx1.bridge_repository_to_namespace("REPO_TEST_01", "env"))

    # Get add repository contract
    print(ctx_1.get_add_repository_contract())

    # Add a new repository (not supported with the demo plugin but showing how to use it based on README configuration provided)
    repository = {
        "name": "my-new-project",
        "project": {
            "name": "Proof Of Concept",
            "key": "POC"
        },
        "configuration": {
            "short": "master; depoy/*",
            "key": "trunkbased-deploy"
        },
        "privileges": {
            "short": "Devs",
            "key": "dev-default"
        }
    }
    template = "hello-scaffold-service"
    template_params = {
        "name": "myproject",
        "desc": "This is a test !",
        "helloworld": False
    }
        
    await ctx_1.add_repository(
        repository,
        template,
        template_params,
        args=None
    )

    # Compliance (see alternative with: compliance_report, compliance_repository, compliance_report_repository)
    r = await ctx_1.compliance(report=True)
    print(json.dumps(r, sort_keys=True, indent=4))

    await core.delete_context(ctx_1)

With statement
^^^^^^^^^^^^^^

As an alternative to create_context / delete_context, you can use:

.. code:: python

    async with core.context("demo", {"user": "test2"}) as ctx:
        pass

Add a new repository
--------------------

Contract
^^^^^^^^

A contract permit to explain what it is required to add a new repository.
A contract is not static and can change mainly with the template involved.

A contract is set with multiple arguments. Each argument is set with the following syntax:

.. code:: bash

    {
        "key": {
            "type": "string" | "suggestion" | "bool",
            "required": true | false,
            "default": "string" | true | false,
            "description": "The user friendly description of this argument",
            "validator": "A python regular expression",
            "values": [{ ... }, ...],
            "roleName": "name of a key in a value stored in values array"
        }
    }

type, required, default and description are mandatory.

validator can be used for string type.

values and roleName are only used with suggestion type.

Answers
^^^^^^^

Answers is an object that provides an answer for all (or subset) of contract arguments.

An answer for one contract argument following this syntax:

.. code:: bash

    {
        "key": "string" | true | false | { one value in values }
    }

Repository name
^^^^^^^^^^^^^^^

The key "name" is reserved in the repository contract. Despite this key doesn't exist in your own repository contract, the provision system will automatically add it on top of the contract.

How can I get all contracts to add a new repository
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code:: python

    ctx.get_add_repository_contract()

Create a new repository
^^^^^^^^^^^^^^^^^^^^^^^

You need to select wich template do you want to use from what is available in get_add_repository_contract().

Once selected you need to fulfill all contracts for repository, template and template parameters. Please read the "Commands available with a plugin" section

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

        async def init(self, core, args):
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

Unit test
^^^^^^^^^^^^^^^^^^

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements_test.txt

coverage run -m unittest discover -s tests -p "*_test.py"

python3 tests/bitbucketcloud_test.py TestBitbucketCloud.test6_fetch_continuous_deployment_config_should_return_branch_available