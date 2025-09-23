import asyncio
import functools
import inspect
import logging
from datetime import datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
)

try:
    from typing import ParamSpec  # Python 3.10+
except ImportError:
    from typing_extensions import ParamSpec  # Python 3.9


# Configure recommended logger
def setup_logger(name: str = "ooflow", level: int = logging.INFO) -> logging.Logger:
    """
    Set up recommended logger with format: date, time, log content

    Args:
        name: logger name
        level: logging level

    Returns:
        configured logger instance
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handler addition
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)

    # Create formatter: date, time, log content
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    # Prevent log propagation to root logger
    logger.propagate = False

    return logger


# Create default logger instance
logger = setup_logger()

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")


class Context:
    incoming_edges: Dict["Node", "Edge"]
    outgoing_edges: Dict["Node", "Edge"]

    def __init__(self):
        self.incoming_edges = {}
        self.outgoing_edges = {}

    def add_incoming_edge(self, edge: "Edge"):
        self.incoming_edges[edge.from_node] = edge

    def add_outgoing_edge(self, edge: "Edge"):
        self.outgoing_edges[edge.to_node] = edge

    def _get_target_queues(
        self,
        target: Union[List["Node"], "Node", None],
        candidate_edges: Dict["Node", "Edge"],
    ) -> List[asyncio.Queue]:
        """Get target queue list, handle common node lookup logic"""
        if target is None:
            return [edge.queue for edge in candidate_edges.values()]

        target_list = target if isinstance(target, list) else [target]
        queues = []
        for node in target_list:
            if node not in candidate_edges:
                logger.error(f"Node {node} not found")
                continue
            queues.append(candidate_edges[node].queue)
        return queues

    def emit_nowait(self, msg: Any, to: Union[List["Node"], "Node", None] = None):
        """Send message to target nodes in non-blocking way, if 'to' is None, send to all successor nodes

        May raise asyncio.QueueFull exception if any successor node's queue is full
        """
        queues = self._get_target_queues(to, self.outgoing_edges)
        for queue in queues:
            queue.put_nowait(msg)

    async def emit(self, msg: Any, to: Union[List["Node"], "Node", None] = None):
        """Send message to target nodes in blocking way, if 'to' is None, send to all successor nodes"""
        queues = self._get_target_queues(to, self.outgoing_edges)
        for queue in queues:
            await queue.put(msg)

    def fetch_nowait(self, from_: Union[List["Node"], "Node", None] = None):
        """fetch message from target nodes in non-blocking way, if 'from_' is None, fetch from all predecessor nodes

        It's a good choice to specify 'from_' as a single node for better performance
        May raise asyncio.QueueEmpty exception if all predecessor node queues are empty
        """
        queues = self._get_target_queues(from_, self.incoming_edges)
        for queue in queues:
            try:
                return queue.get_nowait()
            except asyncio.QueueEmpty:
                continue

        raise asyncio.QueueEmpty

    async def fetch(
        self,
        from_: Union[List["Node"], "Node", None] = None,
        check_interval: float = 0.005,
    ):
        """fetch message from target nodes in blocking way, if 'from_' is None, fetch from all predecessor nodes

        If 'from_' specifies multiple nodes, will first try non-blocking retrieval
        If all nodes are empty, will check all nodes at check_interval (seconds) intervals
        """
        queues = self._get_target_queues(from_, self.incoming_edges)

        if len(queues) == 1:
            return await queues[0].get()

        while True:
            for queue in queues:
                try:
                    return queue.get_nowait()
                except asyncio.QueueEmpty:
                    continue
            await asyncio.sleep(check_interval)


class Node:
    """
    ooflow helper decorator
    """

    _func: Callable[P, Awaitable[R]]
    _bound: Optional[Callable[..., Awaitable[R]]]
    context: Optional[Context]

    def __init__(
        self, func: Callable[P, Awaitable[R]], context: Optional[Context] = None
    ):
        if not asyncio.iscoroutinefunction(func):
            raise ValueError(f"function '{func.__name__}' must be an async function")

        self._validate_function_signature(func)

        functools.update_wrapper(
            self, func
        )  # copy __name__ / __doc__ / __wrapped__ etc.
        self._func = func
        self._bound = None
        self.context = context if context is not None else Context()

    def _validate_function_signature(self, func: Callable):
        """Validate function signature, ensure parameters meet requirements"""
        sig = inspect.signature(func)
        params = list(sig.parameters.values())

        # Simplified validation: only check parameter count and Context type
        if len(params) == 1:
            # One parameter: must be Context type
            context_param = params[0]
            if context_param.annotation != Context:
                raise ValueError(
                    f"Function '{func.__name__}' parameter '{context_param.name}' "
                    f"must be ooflow.Context type, but got {context_param.annotation}"
                )
        elif len(params) == 2:
            # Two parameters: second parameter must be Context type
            context_param = params[1]
            if context_param.annotation != Context:
                raise ValueError(
                    f"Function '{func.__name__}' second parameter '{context_param.name}' "
                    f"must be ooflow.Context type, but got {context_param.annotation}"
                )
        else:
            # Other number of parameters are not allowed
            raise ValueError(
                f"Function '{func.__name__}' can only have 1 or 2 parameters, "
                f"but got {len(params)} parameters"
            )

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        if self._bound:
            return await self._bound(*args, **kwargs)
        return await self._func(*args, **kwargs)

    def __get__(self, instance, owner):
        """Only class methods and instance methods reach here, static methods and regular functions don't"""

        if self._bound:
            return self

        self._bound = self._func.__get__(instance, owner)
        return self

    def to(self, *args: "Node") -> Tuple["Node", Tuple["Node", ...]]:
        for arg in args:
            if not isinstance(arg, Node):
                raise ValueError(f"params must be of type Node, but got {arg}")
        return (self, args)


class Edge:
    from_node: Optional[Node]
    to_node: Optional[Node]
    queue: asyncio.Queue

    def __init__(self, from_node: Optional[Node], to_node: Optional[Node]):
        self.from_node = from_node
        self.to_node = to_node
        self.queue = asyncio.Queue()


class OoFlow:
    Yin: Context  # Yin, invisible to user, actually the end point of the entire OoFlow, used to simplify OoFlow coding logic
    Yang: Context  # Yang, invisible to user, actually the start point of the entire OoFlow, used to simplify OoFlow coding logic
    end_nodes: Set[Node]  # End nodes calculated from user's input
    start_nodes: Set[Node]  # Start nodes calculated from user's input
    graphs: Set[
        "OoFlow.NodeGroup"
    ]  # Multiple graphs are allowed, calculated from user's input
    running_tasks: List[asyncio.Task]
    running: bool

    class NodeGroup:
        id: int
        parent: Optional["OoFlow.NodeGroup"]
        nodes: Set[Node]

        def __init__(self, id: int):
            self.id = id
            self.nodes = set()
            self.parent = None

        def set_parent(self, parent: "OoFlow.NodeGroup"):
            self.parent = parent

        def add_node(self, node: Node):
            self.nodes.add(node)

    def __init__(self, *args: Tuple[Node, Tuple[Node, ...]]):
        """Construct OoFlow instance

        *args represents node relationships in adjacency list format, for example:
        A -> (B, C)
        B -> (C, D)
        C -> (D)

        Currently no restrictions on graph shape, even two or more separate graphs are fine
        The only disallowed case is cyclic graph at start/end (no nodes with in-degree 0 or out-degree 0)
        """

        self.start_nodes = set()
        self.end_nodes = set()
        self.graphs = set()
        self.running_tasks = []
        self.running = False

        def cmp_group_index(x: Optional[int], y: Optional[int]) -> int:
            if x is None:
                return 1
            if y is None:
                return -1
            return (x > y) - (x < y)

        # Use union-find for graph partitioning
        group_list: List["OoFlow.NodeGroup"] = []
        node2group: Dict[Node, int] = {}
        dedup: Set[str] = set()

        for adj in args:
            from_: Node = adj[0]
            to_list: Tuple[Node, ...] = adj[1]

            # First calculate which NodeGroup to merge into
            adj_group_index: list[Optional[int]] = sorted(
                [node2group.get(from_)] + [node2group.get(to) for to in to_list],
                key=functools.cmp_to_key(cmp_group_index),
            )
            final_group_index: Optional[int] = adj_group_index[0]
            if final_group_index is None:
                final_group_index = len(group_list)
                group_list.append(OoFlow.NodeGroup(final_group_index))

            # Update NodeGroup each node belongs to
            for node in [from_] + list(to_list):
                if node not in node2group:
                    node2group[node] = final_group_index
                    group_list[final_group_index].add_node(node)
                elif node2group[node] != final_group_index:
                    parent = group_list[node2group[node]].parent
                    if parent is None or parent.id > final_group_index:
                        group_list[node2group[node]].set_parent(
                            group_list[final_group_index]
                        )

            # Update each node's Context, establish Edge connections
            for to in to_list:
                adj_key: str = f"{from_.__qualname__}|{to.__qualname__}"
                if adj_key in dedup:
                    continue
                dedup.add(adj_key)

                edge = Edge(from_, to)
                from_.context.add_outgoing_edge(edge)
                to.context.add_incoming_edge(edge)

        # Calculate Graph and check if any graph is missing start or end nodes
        gid2start: Dict[int, bool] = {}
        gid2end: Dict[int, bool] = {}
        for group in group_list:
            ancestor = group
            while ancestor.parent is not None:
                ancestor = ancestor.parent

            gid = ancestor.id  # gid means 'group id'
            for node in group.nodes:
                ancestor.add_node(node)
                if len(node.context.incoming_edges) == 0:
                    gid2start[gid] = True
                    self.start_nodes.add(node)
                if len(node.context.outgoing_edges) == 0:
                    gid2end[gid] = True
                    self.end_nodes.add(node)

            gid2start[gid] = gid2start.get(gid, False)
            gid2end[gid] = gid2end.get(gid, False)
            self.graphs.add(ancestor)

        for gid, has_start_node in gid2start.items():
            if not has_start_node:
                raise ValueError(f"Graph {gid} has no start node")
        for gid, has_end_node in gid2end.items():
            if not has_end_node:
                raise ValueError(f"Graph {gid} has no end node")

        self.Yang = Context()
        self.Yin = Context()

        for node in self.start_nodes:
            edge = Edge(self.Yang, node)
            self.Yang.add_outgoing_edge(edge)
            node.context.add_incoming_edge(edge)
        for node in self.end_nodes:
            edge = Edge(node, self.Yin)
            node.context.add_outgoing_edge(edge)
            self.Yin.add_incoming_edge(edge)

    def run(self):
        # run all nodes in all graphs
        for graph in self.graphs:
            for node in graph.nodes:
                self.running_tasks.append(asyncio.create_task(node(node.context)))
        self.running = True

    def stop(self):
        for task in self.running_tasks:
            task.cancel()
        self.running = False
        self.running_tasks = []

    def emit_nowait(self, msg: Any, to: Union[List[Node], Node, None] = None):
        self.Yang.emit_nowait(msg, to)

    async def emit(self, msg: Any, to: Union[List[Node], Node, None] = None):
        await self.Yang.emit(msg, to)

    def fetch_nowait(self, from_: Union[List["Node"], "Node", None] = None):
        return self.Yin.fetch_nowait(from_)

    async def fetch(
        self,
        from_: Union[List["Node"], "Node", None] = None,
        check_interval: float = 0.005,
    ):
        return await self.Yin.fetch(from_, check_interval)


def create(*args: Tuple[Node, Tuple[Node, ...]]) -> OoFlow:
    return OoFlow(*args)
