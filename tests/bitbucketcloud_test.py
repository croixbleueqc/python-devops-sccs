import unittest
import json
import asynctest
import mock
import pytest

import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))

# from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, MagicMock
from unittest.mock import patch

from devops_sccs.plugins.bitbucketcloud import BitbucketCloud, init_plugin
from devops_sccs.realtime.hookserver import app_sccs, HookServer
from devops_sccs.core import Core as SccsCore
from aiobitbucket.bitbucket import Bitbucket
from httpx import AsyncClient
from fastapi import Request 
from aiobitbucket.typing.webhooks.webhook import event_t as HookEvent_t

class AsyncIterator:
    def __init__(self, items):    
        self.items = items    

    def __aiter__(self):    
        return self

    async def __aiter__(self):    
        for item in self.items:    
            yield item    

class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)

def getMockRepo(name):
    result=Mock()
    result.permission='admin'
    result.repository=Mock()
    result.repository.name=name
    return result

class TestBitbucketCloud(asynctest.TestCase):
    
    def setUp(self):
        with open('tests/bitbucketcloud_test_core.json', 'r') as f:
            self.config = json.load(f)

        with open('tests/bitbucketcloud_test_config.json', 'r') as f:
            self.args = json.load(f)

        with open('tests/private_config.json', 'r') as f:
            privateArgs = json.load(f)

        # self.args['user']="test_user"
        # self.args['apikey']="abcd"
        # self.args["author"]="test user <test.user@company.com>"
        
        self.args['user']=privateArgs['user']
        self.args['apikey']=privateArgs['apikey']
        self.args["author"]=privateArgs['author']

        plugin=init_plugin()

        self.core = None
        self.bitbucketPlugin=plugin[1]

    async def tearDown(self):
        await self.bitbucketPlugin.cleanup()
        if self.core is not None:
            await self.core.cleanup()

    async def test1_plugin_init(self):
        #Arrange
        #Test
        plugin=init_plugin()

        #Assert
        self.assertEqual(plugin[0], "bitbucketcloud")
        self.assertTrue(isinstance(plugin[1], BitbucketCloud))

    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test2_class_init_with_hookserver_should_succeed(self, mock_uvicorn):
        #Arrange
        self.core = await SccsCore.create(self.config)
        
        #Test
        result = await self.bitbucketPlugin.init(self.core, self.args)

        #Assert
        self.assertTrue(True)


    async def test3_class_init_without_hookserver_should_succeed(self):
        #Arrange
        self.config["hook_server"]=None
        self.core = await SccsCore.create(self.config)
        
        #Test
        result = await self.bitbucketPlugin.init(self.core, self.args)

    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test4_multiple_open_sessions_should_be_shared(self,mock_uvicorn):
        #Arrange
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        sessionId=self.bitbucketPlugin.get_session_id(self.args)

        #Test
        await self.bitbucketPlugin.open_session(sessionId, self.args)
        await self.bitbucketPlugin.open_session(sessionId, self.args)

        #Assert
        session=self.bitbucketPlugin.get_session(sessionId)
        self.assertTrue(session['shared-session']==2)

    @mock.patch('devops_sccs.plugins.bitbucketcloud.Bitbucket')
    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test5_get_bitbucket_repositories_bitbucket_session(self,mock_uvicorn, mock_bitbucket):
        #Arrange
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        sessionId=self.bitbucketPlugin.get_session_id(self.args)
        await self.bitbucketPlugin.open_session(sessionId, self.args)
        session=self.bitbucketPlugin.get_session(sessionId)

        rep1=getMockRepo('helloworld')
        rep2=getMockRepo('helloworld2')
        bitbucket_instance=mock_bitbucket.return_value
        bitbucket_instance.close_session = AsyncMock()
        bitbucket_instance.user.permissions.repositories.get.return_value= AsyncIterator([rep1,rep2])

        #Test
        result=await self.bitbucketPlugin.get_repositories(session, self.args)

        #Assert
        self.assertTrue(len(result)==2)
        self.assertTrue(result[0].name=='helloworld')
        self.assertTrue(result[1].name=='helloworld2')

    # @mock.patch('devops_sccs.plugins.bitbucketcloud.Bitbucket')
    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test6_fetch_continuous_deployment_config_should_return_branch_available(self,mock_uvicorn):

        #Arrange
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        # Todo : mock repository and branches used

        #Test
        result=await self.bitbucketPlugin._fetch_continuous_deployment_config('aiobitbucket-wip')

        #Assert
        self.assertTrue(result['master'] is not None)
        self.assertTrue(result['deploy/dev'] is not None)
        self.assertTrue(result['deploy/prod'] is not None)

    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    @pytest.mark.anyio
    async def test7_handle_push_from_bitbucket(self, mock_uvicorn):
        #Arrange
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)

        path="/bitbucketcloud/hooks/repo"
        headers={"X-Event-Key": "repo:push"}
        push_payload = {
            'actor': 'marcouxm',
            'repository': {
                "type": "repository",
                "full_name": "croixbleue/aiobitbucket-wip",
                "workspace": {"slug":"croixbleue"},
                "name":"aiobitbucket-wip"
            },
            "push": {
                "changes": [
                    {
                        "created":True,
                        "new":{
                            "name":"deploy/dev"
                        }
                    }
                ]
            }
        }

        #Test
        async with AsyncClient(app=app_sccs) as client:
            response = await client.post(path,headers=headers,json=push_payload)

        #Assert
            self.assertTrue(response.status_code == 200)

if __name__ == '__main__':
    unittest.main()


# Next tests to be created in priority

    # async def testX_Given_get_continuous_deployment_config_When_another_user_get_same_repo_Then_data_should_be_get_from_cache(self):
    
    # async def testX_Given_get_continuous_deployment_config_When_handle_push_deploydev_Then_data_should_be_get_updated_in_cache(self):

    # async def testX_Given_new_merge_in_master_When_hook_handle_commit_status_Then_cache_version_avalaible_with_new_version(self):

    # async dev testX_Given_repo_exist_When_hook_delete_repo_Then_repo_is_deleted_from_cache(self):

    # async dev testX_When_trigger_continuous_deployment_with_X_condition_Then_new_pull_request_is_created(self):

    # async def testX_Given_fetch_continuous_deployment_config_When_another_user_get_same_repo_after_cache_expire_Then_data_should_be_fetch_from_bitbucket(self):

    # async def testX_Given_custom_hook_call_When_cache_is_corrupted_Then_cache_is_cleared(self):  
        # TBD : hook delete repo could be used ?
        