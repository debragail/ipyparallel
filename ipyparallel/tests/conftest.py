"""pytest fixtures"""
import inspect
import logging
import os
import sys
from subprocess import check_call
from subprocess import STDOUT
from tempfile import TemporaryDirectory
from unittest import mock

import IPython.paths
import pytest
import zmq
from IPython.core.profiledir import ProfileDir
from IPython.terminal.interactiveshell import TerminalInteractiveShell
from IPython.testing.tools import default_config
from traitlets.config import Config

import ipyparallel as ipp
from . import setup
from . import teardown


@pytest.fixture(autouse=True, scope="session")
def ipython_dir():
    with TemporaryDirectory(suffix="dotipython") as td:
        with mock.patch.dict(os.environ, {"IPYTHONDIR": td}):
            assert IPython.paths.get_ipython_dir() == td
            pd = ProfileDir.create_profile_dir_by_name(td, name="default")
            # configure fast heartbeats for quicker tests with small numbers of local engines
            with open(os.path.join(pd.location, "ipcontroller_config.py"), "w") as f:
                f.write("c.HeartMonitor.period = 200")
            yield td


def pytest_collection_modifyitems(items):
    """This function is automatically run by pytest passing all collected test
    functions.

    We use it to add asyncio marker to all async tests and assert we don't use
    test functions that are async generators which wouldn't make sense.
    """
    for item in items:
        if inspect.iscoroutinefunction(item.obj):
            item.add_marker('asyncio')
        assert not inspect.isasyncgenfunction(item.obj)


@pytest.fixture(scope="session")
def cluster(request):
    """Setup IPython parallel cluster"""
    setup()
    request.addfinalizer(teardown)


@pytest.fixture(scope='session')
def ipython():
    config = default_config()
    config.TerminalInteractiveShell.simple_prompt = True
    shell = TerminalInteractiveShell.instance(config=config)
    return shell


@pytest.fixture()
def ipython_interactive(request, ipython):
    """Activate IPython's builtin hooks

    for the duration of the test scope.
    """
    with ipython.builtin_trap:
        yield ipython


@pytest.fixture(autouse=True)
def Context():
    ctx = zmq.Context.instance()
    try:
        yield ctx
    finally:
        ctx.destroy()


@pytest.fixture
def Cluster(request, io_loop):
    """Fixture for instantiating Clusters"""

    def ClusterConstructor(**kwargs):
        log = logging.getLogger(__file__)
        log.setLevel(logging.DEBUG)
        log.handlers = [logging.StreamHandler(sys.stdout)]
        kwargs['log'] = log
        engine_launcher_class = kwargs.get("engine_launcher_class")

        cfg = kwargs.setdefault("config", Config())
        cfg.EngineLauncher.engine_args = ['--log-level=10']
        cfg.ControllerLauncher.controller_args = ['--log-level=10']
        kwargs.setdefault("controller_args", ['--ping=250'])

        c = ipp.Cluster(**kwargs)
        assert c.config is cfg
        request.addfinalizer(c.stop_cluster_sync)
        return c

    yield ClusterConstructor


@pytest.fixture(scope="session")
def ssh_dir(request):
    """Start the ssh service with docker-compose

    Fixture returns the directory
    """
    repo_root = os.path.abspath(os.path.join(ipp.__file__, os.pardir, os.pardir))
    ci_directory = os.environ.get("CI_DIR", os.path.join(repo_root, 'ci'))
    ssh_dir = os.path.join(ci_directory, "ssh")
    # build image
    check_call(["docker", "compose", "build"], cwd=ssh_dir)
    # launch service
    check_call(["docker", "compose", "up", "-d"], cwd=ssh_dir)
    # shutdown service when we exit
    request.addfinalizer(lambda: check_call(["docker", "compose", "down"], cwd=ssh_dir))
    return ssh_dir


@pytest.fixture
def ssh_key(tmpdir, ssh_dir):
    key_file = tmpdir.join("id_rsa")
    check_call(
        [
            'docker',
            'compose',
            'cp',
            'sshd:/home/ciuser/.ssh/id_rsa',
            key_file,
        ],
        cwd=ssh_dir,
    )
    os.chmod(key_file, 0o600)
    with key_file.open('r') as f:
        assert 'PRIVATE KEY' in f.readline()
    return str(key_file)
