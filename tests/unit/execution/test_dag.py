"""Unit tests for DAG execution."""

from trellis.execution.dag import DAGExecutor, TaskStatus


def test_simple_dag_execution():
    """Test simple DAG execution."""
    tasks = {
        "task1": {"id": "task1", "tool": "mock", "inputs": {}, "await": []},
        "task2": {"id": "task2", "tool": "mock", "inputs": {}, "await": ["task1"]},
    }

    executor = DAGExecutor()
    results = executor.execute(tasks)

    assert "task1" in results
    assert "task2" in results


def test_task_status():
    """Test task status enumeration."""
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.SUCCESS.value == "success"
    assert TaskStatus.FAILED.value == "failed"
