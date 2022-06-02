import multiprocessing
import uvicorn
from multiprocessing import Manager
import logging
import asyncio
from fastapi import FastAPI
import os
import signal

# sccs fast api server entrypoint
app_sccs = FastAPI()


class HookServer:
    """
    Class that run a uvicorn server with an async cache manager.
    """

    def __init__(self, settings):
        self.host = settings["host"]
        self.port = settings["port"]
        self.manager = Manager()
        self.lock = asyncio.Lock()

    async def start_server(self):
        async with self.lock:
            logging.debug(
                [{"path": route.path, "name": route.name} for route in app_sccs.routes]
            )

            self.threadedServer = multiprocessing.Process(
                target=uvicorn.run,
                args=(app_sccs,),
                kwargs={"host": self.host, "port": self.port, "access_log": True},
                daemon=True,
            )
            self.threadedServer.start()

    def stop_server(self):
        try:
            os.kill(self.threadedServer.pid, signal.SIGINT)
        except KeyboardInterrupt:
            self.threadedServer.close()

    def create_dict(self):
        return self.manager.dict()

    def __del__(self):

        if hasattr(self, "threadedServer"):
            if self.threadedServer.is_alive():
                self.threadedServer.terminate()
