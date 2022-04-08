import copy
import multiprocessing
import unittest
import json
import asynctest
import mock
import pytest
import time

import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))

# from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, MagicMock
from unittest.mock import patch

from devops_sccs.plugins.bitbucketcloud import BitbucketCloud, init_plugin
from devops_sccs.realtime.hookserver import app_sccs
from devops_sccs.core import Core as SccsCore
from aiobitbucket.bitbucket import Bitbucket
from httpx import AsyncClient
from cachetest_server import app_test , CacheTest_Server
from cachetest_plugina import CacheTest_PluginA

from fastapi.testclient import TestClient
from devops_sccs.typing import cd as typing_cd
from aiobitbucket.typing.webhooks.webhook import event_t as HookEvent_t
from devops_sccs.cache import AsyncCache

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

def getMockEnvironmentConfig(environment,version="qwerty123456",readonly=False,pullrequest="foo.bar/pr/123456789qwerty"):
    result = Mock(spec=typing_cd.EnvironmentConfig)
    result.environment = environment
    result.version = version
    result.readonly = readonly
    result.pullrequest = pullrequest
    result.buildstatus = "SUCCESSFUL"
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
        self.args["watcher"]["user"] = privateArgs['user']
        self.args["watcher"]["pwd"] = privateArgs['apikey']

        plugin=init_plugin()

        self.core = None
        self.bitbucketPlugin=plugin[1]
        self.hookPath = f"http://{self.config['hook_server']['host']}:{self.config['hook_server']['port']}"

    async def tearDown(self):
        await self.bitbucketPlugin.cleanup()
        if self.core is not None:
            await self.core.cleanup()
            time.sleep(.5)

    async def getSession2(self):
        
        with open('tests/private_config.json', 'r') as f:
            privateArgs = json.load(f)
        
        self.args['user']=privateArgs['user']
        self.args['apikey']=privateArgs['apikey2']
        self.args["author"]=privateArgs['author']

        sessionId=self.bitbucketPlugin.get_session_id(self.args)
        await self.bitbucketPlugin.open_session(sessionId, self.args)
        session=self.bitbucketPlugin.get_session(sessionId)
        return session

    async def test01_plugin_init(self):
        print(1)
        #Arrange
        #Test
        plugin=init_plugin()

        #Assert
        self.assertEqual(plugin[0], "bitbucketcloud")
        self.assertIsInstance(plugin[1], BitbucketCloud)

   
    async def test02_class_init_with_hookserver_should_succeed(self):
        print(2)
        #Arrange
        self.core = await SccsCore.create(self.config)
        
        #Test
        result = await self.bitbucketPlugin.init(self.core, self.args)

        #Assert
        self.assertTrue(True)


    async def test03_class_init_without_hookserver_should_succeed(self):
        print(3)
        #Arrange
        self.config["hook_server"]=None
        self.core = await SccsCore.create(self.config)
        
        #Test
        result = await self.bitbucketPlugin.init(self.core, self.args)

    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test04_multiple_open_sessions_should_be_shared(self,mock_uvicorn):
        print(4)
        #Arrange
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        sessionId=self.bitbucketPlugin.get_session_id(self.args)

        #Test
        await self.bitbucketPlugin.open_session(sessionId, self.args)
        await self.bitbucketPlugin.open_session(sessionId, self.args)

        #Assert
        session=self.bitbucketPlugin.get_session(sessionId)
        self.assertEqual(session['shared-session'],2)

    @mock.patch('devops_sccs.plugins.bitbucketcloud.Bitbucket')
    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test05_get_bitbucket_repositories_bitbucket_session(self,mock_uvicorn,mock_bitbucket):
        print(5)
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
        self.assertEqual(len(result),2)
        self.assertEqual(result[0].name,'helloworld')
        self.assertEqual(result[1].name,'helloworld2')

    # @mock.patch('devops_sccs.plugins.bitbucketcloud.Bitbucket')
    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test06_fetch_continuous_deployment_config_should_return_branch_available(self,mock_uvicorn):

        #Arrange
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        print(6)
        # Todo : mock repository and branches used

        #Test 
        result=await self.bitbucketPlugin._fetch_continuous_deployment_config(**{'repository':self.args['test_repo']["name"]})

        #Assert
        self.assertIsNotNone(result['master'] )
        self.assertIsNotNone(result['deploy/dev'] )
        self.assertIsNotNone(result['deploy/prod'] )
    
    @pytest.mark.anyio
    async def test07_handle_push_from_bitbucket(self):
        #Arrange
        print(7)
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        #import pdb; pdb.set_trace()

        testRepo = self.args['test_repo']["name"]
        testTeam = self.args['test_repo']["team"]


        path="/bitbucketcloud/hooks/repo"
        headers={"X-Event-Key": "repo:push"}
        push_payload = {
            'actor': 'smithj',
            'repository': {
                "type": "repository",
                "full_name": f'{testTeam}/{testRepo}',
                "workspace": {"slug":f"{testTeam}"},
                "name":f'{testRepo}'
            },
            "push": {
                "changes": [
                    {
                        "created":True,
                        "new":{
                            "name":"deploy/dev",
                            "target":{
                                "message": "deploy version f00ddeadbeeff00d"
                            }
                        }
                    }
                ]
            }
        }

        #environementConfigPre = await self.bitbucketPlugin.cache["continuousDeploymentConfig"][testRepo]
        #Test
        time.sleep(.5)
        async with AsyncClient() as client:
            response = await client.post(self.hookPath+path,headers=headers,json=push_payload)

        #Assert
            #import pdb; pdb.set_trace()
            environementConfigResults = self.bitbucketPlugin.cache["continuousDeploymentConfig"].get(testRepo)
            self.assertEqual(response.status_code , 200)
            self.assertIsNotNone(environementConfigResults)

    #@mock.patch('devops_sccs.plugins.bitbucketcloud.Bitbucket')
    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    async def test08_Given_get_continuous_deployment_config_When_another_user_get_same_repo_Then_data_should_be_get_from_cache(self,mock_uvicorn):  
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        print(8)
        sessionId=self.bitbucketPlugin.get_session_id(self.args)
        await self.bitbucketPlugin.open_session(sessionId, self.args)
        session=self.bitbucketPlugin.get_session(sessionId)
        
        session2=await self.getSession2()

        testRepo = self.args['test_repo']['name']

        original = await self.bitbucketPlugin.get_continuous_deployment_config(session,testRepo)
        
        edited = copy.copy(original)
        
        edited['master'].version = "deadf00dbeef"

        self.bitbucketPlugin.cache["continuousDeploymentConfig"][testRepo] = edited 
        result = await self.bitbucketPlugin.get_continuous_deployment_config(session2,testRepo)
        
        self.assertTrue(result['master']==edited['master'])

    @pytest.mark.anyio
    async def test09_Given_get_continuous_deployment_config_When_handle_push_deploydev_Then_data_should_be_get_updated_in_cache(self):
        self.core = await SccsCore.create(self.config)
        await self.bitbucketPlugin.init(self.core, self.args)
        print(9)
        testRepo = self.args['test_repo']["name"]
        testTeam = self.args['test_repo']["team"]

        version = "f00ddeadbeeff00d"

        path="/bitbucketcloud/hooks/repo"
        headers={"X-Event-Key": "repo:push"}
        push_payload = {
            'actor': 'smithj',
            'repository': {
                "type": "repository",
                "full_name": f'{testTeam}/{testRepo}',
                "workspace": {"slug":f"{testTeam}"},
                "name":f'{testRepo}'
            },
            "push": {
                "changes": [
                    {
                        "created":True,
                        "new":{
                            "name":"deploy/dev",
                            "target":{
                                "message": f"deploy version {version}"
                            }
                        }
                    }
                ]
            }
        }

       # environementConfigResults = await self.bitbucketPlugin.cache["continuousDeploymentConfig"][testRepo]
        #Test
        time.sleep(.5)
        async with AsyncClient(base_url=self.hookPath) as client:
            response = await client.post(path,headers=headers,json=push_payload)

        #Assert
           
            environementConfigResults = self.bitbucketPlugin.cache["continuousDeploymentConfig"].get(testRepo)
            self.assertEqual(response.status_code , 200)
            self.assertIsNotNone(environementConfigResults)
            #import pdb; pdb.set_trace()
            self.assertEqual(environementConfigResults["deploy/dev"].version , version)

    @mock.patch('devops_sccs.realtime.hookserver.uvicorn')
    @pytest.mark.anyio
    async def test10_Given_cache_ref_class_containing_it_When_sent_to_other_thread_Then_should_sync_multi_thread(self,mock_uvicorn):
        print(10)
        cache_key = 'a'
        value_key = 'a'
        
        def assert_value(expected_value,environement):
            result = a.cache[cache_key].get(value_key)
            self.assertEqual(result,expected_value,f"\"{result}\" is not equal \"{expected_value}\" in {environement}")

        #testing single process
        test_server = CacheTest_Server()
        a = CacheTest_PluginA()
        a.init(test_server)
        await a.get_a(value_key)
        assert_value('abc',"single process")
        
        a.b = 'a'

        async with AsyncClient(app=app_test,base_url="http://localhost:5002") as client:
            response = await client.post(a.path)
            self.assertEqual(response.status_code ,200)
            assert_value('aac','client in async post')

        test_server.run_test_edit_cache_alone(a.cache[cache_key],value_key,'c')
        assert_value('c','edit directly from other process')

        test_server.run_test_edit_cache_in_dict(a.cache,cache_key,value_key,'d')
        assert_value('d','edit dictionary containing cache from other process')

        test_server.run_test_edit_from_class(a,cache_key,value_key,'e')
        assert_value('e','edit class containing cache from other process')

        a.b = 'f'
        test_server.run_test_run_hypercorn(a.path)
        assert_value('afc','hypercorn post')
        
        a.b = 'g'
        test_server.run_test_run_uvicorn(a.path)
        assert_value('agc','uvicorn post')

        
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
        