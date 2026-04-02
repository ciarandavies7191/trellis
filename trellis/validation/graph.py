"""
trellis.validation.graph — DAG validation and topological sorting.

Provides two public functions:

    pipeline_execution_waves(pipeline) -> list[list[Task]]
        Validates that the task graph is acyclic and returns tasks grouped
        into parallel execution waves.

    plan_execution_waves(plan) -> list[list[SubPipeline]]
        Validates that the sub-pipeline graph is acyclic and returns
        sub-pipelines grouped into parallel execution waves.

Both use Kahn's algorithm. A cycle is detected implicitly when the algorithm
exhausts the ready queue before processing all nodes.

For cycle diagnostics we also provide:

    find_cycle(graph) -> list[str]
        DFS-based cycle finder that returns the cycle as an ordered list of
        node IDs for human-readable error messages.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TypeVar

from trellis.exceptions import CycleError, ContractError
from trellis.models.pipeline import Pipeline, Task
from trellis.models.plan import Plan, SubPipeline

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

#: Adjacency list: node_id → set of node_ids that depend on it (successors).
Graph = dict[str, set[str]]

N = TypeVar("N")  # generic node type for the wave builder


# ---------------------------------------------------------------------------
# Generic Kahn's algorithm
# ---------------------------------------------------------------------------


def _kahn_waves(
    node_ids: list[str],
    predecessors: dict[str, set[str]],
) -> list[list[str]]:
    """
    Run Kahn's topological sort and return nodes grouped into execution waves.

    A wave is a maximal set of nodes whose predecessors have all been
    processed in earlier waves — i.e. nodes that can run in parallel.

    Args:
        node_ids:     All node ids in the graph.
        predecessors: Mapping of node_id → set of node_ids it must wait for.

    Returns:
        A list of waves, each wave being a list of node IDs.

    Raises:
        CycleError: if not all nodes can be processed (cycle detected).
    """
    # in-degree per node
    in_degree: dict[str, int] = {nid: len(predecessors.get(nid, set())) for nid in node_ids}

    # successors map (needed to decrement in-degrees as nodes complete)
    successors: dict[str, set[str]] = defaultdict(set)
    for nid in node_ids:
        for pred in predecessors.get(nid, set()):
            successors[pred].add(nid)

    # seed queue with all zero-in-degree nodes
    queue: deque[str] = deque(nid for nid in node_ids if in_degree[nid] == 0)
    waves: list[list[str]] = []
    processed: int = 0

    while queue:
        # Everything currently in the queue forms the next wave
        wave = list(queue)
        queue.clear()
        waves.append(wave)
        processed += len(wave)

        for nid in wave:
            for successor in successors[nid]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

    if processed != len(node_ids):
        # Some nodes were never reachable — cycle present
        remaining = [nid for nid in node_ids if in_degree[nid] > 0]
        cycle = find_cycle({nid: predecessors.get(nid, set()) for nid in remaining})
        raise CycleError(
            f"Cycle detected among nodes: {remaining}. "
            f"Cycle path: {' → '.join(cycle) if cycle else 'unknown'}",
            cycle=cycle,
        )

    return waves


# ---------------------------------------------------------------------------
# Cycle finder (DFS) — used only for diagnostics
# ---------------------------------------------------------------------------


def find_cycle(predecessor_graph: dict[str, set[str]]) -> list[str]:
    """
    Find and return one cycle in a graph as an ordered list of node IDs.

    Args:
        predecessor_graph: node_id → set of predecessor node_ids.

    Returns:
        A list of node IDs forming a cycle, starting and ending at the same
        node. Empty list if no cycle is found (shouldn't happen if called
        from _kahn_waves on a known-cyclic subgraph).
    """
    # Convert predecessor graph to successor graph for DFS
    successors: dict[str, set[str]] = defaultdict(set)
    nodes = set(predecessor_graph.keys())
    for nid, preds in predecessor_graph.items():
        for pred in preds:
            successors[pred].add(nid)
            nodes.add(pred)

    visited: set[str] = set()
    stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        visited.add(node)
        stack.add(node)
        path.append(node)

        for neighbour in successors.get(node, set()):
            if neighbour not in visited:
                result = dfs(neighbour)
                if result is not None:
                    return result
            elif neighbour in stack:
                # Found the cycle — slice from where it begins
                cycle_start = path.index(neighbour)
                return path[cycle_start:] + [neighbour]

        stack.discard(node)
        path.pop()
        return None

    for node in nodes:
        if node not in visited:
            result = dfs(node)
            if result is not None:
                return result

    return []


# ---------------------------------------------------------------------------
# Pipeline-level validation
# ---------------------------------------------------------------------------


def pipeline_execution_waves(pipeline: Pipeline) -> list[list[Task]]:
    """
    Validate a Pipeline's task graph and return tasks grouped into parallel
    execution waves.

    Wave 0 contains all root tasks (no upstream dependencies). Wave N contains
    all tasks whose dependencies were fully satisfied by waves 0..N-1.

    Args:
        pipeline: A structurally valid Pipeline model instance.

    Returns:
        A list of waves. Each wave is a list of Task objects that can execute
        concurrently.

    Raises:
        CycleError: if any cycle exists in the task dependency graph.
    """
    task_map = pipeline.task_map()
    node_ids = list(task_map.keys())

    # Build predecessor map: task_id → set of task_ids it waits for
    predecessors: dict[str, set[str]] = {
        tid: task_map[tid].upstream_task_ids()
        for tid in node_ids
    }

    id_waves = _kahn_waves(node_ids, predecessors)

    # Map back to Task objects, preserving wave structure
    return [[task_map[tid] for tid in wave] for wave in id_waves]


# ---------------------------------------------------------------------------
# Plan-level validation
# ---------------------------------------------------------------------------


def _build_plan_predecessors(plan: Plan) -> dict[str, set[str]]:
    """
    Derive predecessor relationships between sub-pipelines from reads/stores.

    A sub-pipeline B depends on sub-pipeline A if any key in B.reads appears
    in A.stores.

    Also validates that no two sub-pipelines declare the same key in stores
    (write conflict).

    Raises:
        ContractError: if two sub-pipelines attempt to write the same key.
    """
    # stores_index: key → sub-pipeline id that writes it
    stores_index: dict[str, str] = {}
    for sp in plan.sub_pipelines:
        for key in sp.stores:
            if key in stores_index:
                raise ContractError(
                    f"Blackboard key {key!r} is declared in `stores` by both "
                    f"{stores_index[key]!r} and {sp.id!r}. "
                    f"Each key may only be written by one sub-pipeline."
                )
            stores_index[key] = sp.id

    # Build predecessor map
    predecessors: dict[str, set[str]] = {sp.id: set() for sp in plan.sub_pipelines}
    for sp in plan.sub_pipelines:
        for key in sp.reads:
            writer = stores_index.get(key)
            if writer is not None and writer != sp.id:
                predecessors[sp.id].add(writer)
            # Unwritten reads are allowed — they may come from plan.inputs
            # or from a prior session; contract.py validates this separately.

    return predecessors


def plan_execution_waves(plan: Plan) -> list[list[SubPipeline]]:
    """
    Validate a Plan's sub-pipeline graph and return sub-pipelines grouped into
    parallel execution waves.

    Wave 0 contains all root sub-pipelines (empty reads, or reads fully
    satisfied by plan.inputs). Each subsequent wave contains sub-pipelines
    whose blackboard dependencies are satisfied by all prior waves.

    Args:
        plan: A structurally valid Plan model instance.

    Returns:
        A list of waves. Each wave is a list of SubPipeline objects that can
        execute concurrently.

    Raises:
        CycleError:     if any cycle exists in the sub-pipeline dependency graph.
        ContractError:  if two sub-pipelines declare the same stores key.
    """
    sp_map = {sp.id: sp for sp in plan.sub_pipelines}
    node_ids = list(sp_map.keys())

    predecessors = _build_plan_predecessors(plan)

    id_waves = _kahn_waves(node_ids, predecessors)

    return [[sp_map[sid] for sid in wave] for wave in id_waves]