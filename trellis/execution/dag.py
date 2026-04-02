"""DAG execution engine for pipelines."""

from typing import Dict, List, Any, Callable, Optional
from dataclasses import dataclass
from enum import Enum


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskResult:
    """Result of task execution."""
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[str] = None
    retries: int = 0


class DAGExecutor:
    """Executes a DAG of tasks."""

    def __init__(self, tool_executor: Optional[Callable] = None):
        """
        Initialize DAG executor.

        Args:
            tool_executor: Callable to execute individual tools
        """
        self.tool_executor = tool_executor
        self.results: Dict[str, TaskResult] = {}

    def execute(
        self,
        tasks: Dict[str, Dict[str, Any]],
        context: Dict[str, Any] = None
    ) -> Dict[str, TaskResult]:
        """
        Execute DAG of tasks.

        Args:
            tasks: Dictionary mapping task IDs to task definitions
            context: Execution context with variables

        Returns:
            Dictionary of task results
        """
        context = context or {}
        self.results = {}

        # Topological sort
        execution_order = self._topological_sort(tasks)

        for task_id in execution_order:
            task = tasks[task_id]

            # Check dependencies
            if not self._dependencies_satisfied(task, task_id):
                self.results[task_id] = TaskResult(
                    task_id=task_id,
                    status=TaskStatus.SKIPPED,
                    error="Dependencies not satisfied"
                )
                continue

            # Execute task
            result = self._execute_task(task_id, task, context)
            self.results[task_id] = result

            # Update context with result
            if result.status == TaskStatus.SUCCESS:
                context[f"{task_id}.output"] = result.output

        return self.results

    def _execute_task(
        self,
        task_id: str,
        task: Dict[str, Any],
        context: Dict[str, Any]
    ) -> TaskResult:
        """Execute a single task."""
        try:
            tool_name = task.get("tool")
            inputs = task.get("inputs", {})

            if self.tool_executor:
                output = self.tool_executor(tool_name, inputs, context)
            else:
                output = None

            return TaskResult(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                output=output
            )
        except Exception as e:
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=str(e)
            )

    def _topological_sort(self, tasks: Dict[str, Dict]) -> List[str]:
        """Topologically sort tasks."""
        visited = set()
        order = []

        def visit(task_id: str):
            if task_id in visited:
                return
            visited.add(task_id)

            task = tasks.get(task_id, {})
            dependencies = task.get("await", []) or []

            for dep in dependencies:
                if dep in tasks:
                    visit(dep)

            order.append(task_id)

        for task_id in tasks:
            visit(task_id)

        return order

    def _dependencies_satisfied(self, task: Dict[str, Any], task_id: str) -> bool:
        """Check if all dependencies are satisfied."""
        dependencies = task.get("await", []) or []

        for dep in dependencies:
            if dep not in self.results:
                return False
            if self.results[dep].status != TaskStatus.SUCCESS:
                return False

        return True
