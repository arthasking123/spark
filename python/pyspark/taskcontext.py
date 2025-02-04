#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from typing import Type, Dict, List, Optional, Union, cast

from pyspark.java_gateway import local_connect_and_auth
from pyspark.resource import ResourceInformation
from pyspark.serializers import read_int, write_int, write_with_length, UTF8Deserializer


class TaskContext(object):

    """
    Contextual information about a task which can be read or mutated during
    execution. To access the TaskContext for a running task, use:
    :meth:`TaskContext.get`.
    """

    _taskContext: Optional["TaskContext"] = None

    _attemptNumber: Optional[int] = None
    _partitionId: Optional[int] = None
    _stageId: Optional[int] = None
    _taskAttemptId: Optional[int] = None
    _localProperties: Optional[Dict[str, str]] = None
    _cpus: Optional[int] = None
    _resources: Optional[Dict[str, ResourceInformation]] = None

    def __new__(cls: Type["TaskContext"]) -> "TaskContext":
        """Even if users construct TaskContext instead of using get, give them the singleton."""
        taskContext = cls._taskContext
        if taskContext is not None:
            return taskContext
        cls._taskContext = taskContext = object.__new__(cls)
        return taskContext

    @classmethod
    def _getOrCreate(cls: Type["TaskContext"]) -> "TaskContext":
        """Internal function to get or create global TaskContext."""
        if cls._taskContext is None:
            cls._taskContext = TaskContext()
        return cls._taskContext

    @classmethod
    def _setTaskContext(cls: Type["TaskContext"], taskContext: "TaskContext") -> None:
        cls._taskContext = taskContext

    @classmethod
    def get(cls: Type["TaskContext"]) -> Optional["TaskContext"]:
        """
        Return the currently active TaskContext. This can be called inside of
        user functions to access contextual information about running tasks.

        Notes
        -----
        Must be called on the worker, not the driver. Returns None if not initialized.
        """
        return cls._taskContext

    def stageId(self) -> int:
        """The ID of the stage that this task belong to."""
        return cast(int, self._stageId)

    def partitionId(self) -> int:
        """
        The ID of the RDD partition that is computed by this task.
        """
        return cast(int, self._partitionId)

    def attemptNumber(self) -> int:
        """ "
        How many times this task has been attempted.  The first task attempt will be assigned
        attemptNumber = 0, and subsequent attempts will have increasing attempt numbers.
        """
        return cast(int, self._attemptNumber)

    def taskAttemptId(self) -> int:
        """
        An ID that is unique to this task attempt (within the same SparkContext, no two task
        attempts will share the same attempt ID).  This is roughly equivalent to Hadoop's
        TaskAttemptID.
        """
        return cast(int, self._taskAttemptId)

    def getLocalProperty(self, key: str) -> Optional[str]:
        """
        Get a local property set upstream in the driver, or None if it is missing.
        """
        return cast(Dict[str, str], self._localProperties).get(key, None)

    def cpus(self) -> int:
        """
        CPUs allocated to the task.
        """
        return cast(int, self._cpus)

    def resources(self) -> Dict[str, ResourceInformation]:
        """
        Resources allocated to the task. The key is the resource name and the value is information
        about the resource.
        """
        return cast(Dict[str, ResourceInformation], self._resources)


BARRIER_FUNCTION = 1
ALL_GATHER_FUNCTION = 2


def _load_from_socket(
    port: Optional[Union[str, int]],
    auth_secret: str,
    function: int,
    all_gather_message: Optional[str] = None,
) -> List[str]:
    """
    Load data from a given socket, this is a blocking method thus only return when the socket
    connection has been closed.
    """
    (sockfile, sock) = local_connect_and_auth(port, auth_secret)

    # The call may block forever, so no timeout
    sock.settimeout(None)

    if function == BARRIER_FUNCTION:
        # Make a barrier() function call.
        write_int(function, sockfile)
    elif function == ALL_GATHER_FUNCTION:
        # Make a all_gather() function call.
        write_int(function, sockfile)
        write_with_length(cast(str, all_gather_message).encode("utf-8"), sockfile)
    else:
        raise ValueError("Unrecognized function type")
    sockfile.flush()

    # Collect result.
    len = read_int(sockfile)
    res = []
    for i in range(len):
        res.append(UTF8Deserializer().loads(sockfile))

    # Release resources.
    sockfile.close()
    sock.close()

    return res


class BarrierTaskContext(TaskContext):

    """
    A :class:`TaskContext` with extra contextual info and tooling for tasks in a barrier stage.
    Use :func:`BarrierTaskContext.get` to obtain the barrier context for a running barrier task.

    .. versionadded:: 2.4.0

    Notes
    -----
    This API is experimental
    """

    _port = None
    _secret = None

    @classmethod
    def _getOrCreate(cls: Type["BarrierTaskContext"]) -> "BarrierTaskContext":
        """
        Internal function to get or create global BarrierTaskContext. We need to make sure
        BarrierTaskContext is returned from here because it is needed in python worker reuse
        scenario, see SPARK-25921 for more details.
        """
        if not isinstance(cls._taskContext, BarrierTaskContext):
            cls._taskContext = object.__new__(cls)
        return cast(BarrierTaskContext, cls._taskContext)

    @classmethod
    def get(cls: Type["BarrierTaskContext"]) -> "BarrierTaskContext":
        """
        Return the currently active :class:`BarrierTaskContext`.
        This can be called inside of user functions to access contextual information about
        running tasks.

        Notes
        -----
        Must be called on the worker, not the driver. Returns None if not initialized.
        An Exception will raise if it is not in a barrier stage.

        This API is experimental
        """
        if not isinstance(cls._taskContext, BarrierTaskContext):
            raise RuntimeError("It is not in a barrier stage")
        return cls._taskContext

    @classmethod
    def _initialize(
        cls: Type["BarrierTaskContext"], port: Optional[Union[str, int]], secret: str
    ) -> None:
        """
        Initialize BarrierTaskContext, other methods within BarrierTaskContext can only be called
        after BarrierTaskContext is initialized.
        """
        cls._port = port
        cls._secret = secret

    def barrier(self) -> None:
        """
        Sets a global barrier and waits until all tasks in this stage hit this barrier.
        Similar to `MPI_Barrier` function in MPI, this function blocks until all tasks
        in the same stage have reached this routine.

        .. versionadded:: 2.4.0

        .. warning:: In a barrier stage, each task much have the same number of `barrier()`
            calls, in all possible code branches.
            Otherwise, you may get the job hanging or a SparkException after timeout.

        Notes
        -----
        This API is experimental
        """
        if self._port is None or self._secret is None:
            raise RuntimeError(
                "Not supported to call barrier() before initialize " + "BarrierTaskContext."
            )
        else:
            _load_from_socket(self._port, self._secret, BARRIER_FUNCTION)

    def allGather(self, message: str = "") -> List[str]:
        """
        This function blocks until all tasks in the same stage have reached this routine.
        Each task passes in a message and returns with a list of all the messages passed in
        by each of those tasks.

        .. versionadded:: 3.0.0

        .. warning:: In a barrier stage, each task much have the same number of `allGather()`
            calls, in all possible code branches.
            Otherwise, you may get the job hanging or a SparkException after timeout.

        Notes
        -----
        This API is experimental
        """
        if not isinstance(message, str):
            raise TypeError("Argument `message` must be of type `str`")
        elif self._port is None or self._secret is None:
            raise RuntimeError(
                "Not supported to call barrier() before initialize " + "BarrierTaskContext."
            )
        else:
            return _load_from_socket(self._port, self._secret, ALL_GATHER_FUNCTION, message)

    def getTaskInfos(self) -> List["BarrierTaskInfo"]:
        """
        Returns :class:`BarrierTaskInfo` for all tasks in this barrier stage,
        ordered by partition ID.

        .. versionadded:: 2.4.0

        Notes
        -----
        This API is experimental
        """
        if self._port is None or self._secret is None:
            raise RuntimeError(
                "Not supported to call getTaskInfos() before initialize " + "BarrierTaskContext."
            )
        else:
            addresses = cast(Dict[str, str], self._localProperties).get("addresses", "")
            return [BarrierTaskInfo(h.strip()) for h in addresses.split(",")]


class BarrierTaskInfo(object):
    """
    Carries all task infos of a barrier task.

    .. versionadded:: 2.4.0

    Attributes
    ----------
    address : str
        The IPv4 address (host:port) of the executor that the barrier task is running on

    Notes
    -----
    This API is experimental
    """

    def __init__(self, address: str) -> None:
        self.address = address
