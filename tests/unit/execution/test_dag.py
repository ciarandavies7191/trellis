"""
Unit tests for the DAG executor.

Tests cover:
- Single task execution
- Task dependency resolution via templates
- Parallel fan-out execution (parallel_over)
- Resolution context management
- Retry mechanism with exponential backoff
- Error handling and edge cases
"""

import pytest

from trellis.execution.dag import ToolRegistry
from trellis.execution.dag import execute_pipeline, TaskError
from trellis.execution.template import ResolutionContext
from trellis.models.pipeline import Pipeline, Task


class TestDAGExecutorBasics:
    """Test basic DAGExecutor functionality."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create tool registry with mock tool."""
        registry = ToolRegistry()
        
        async def mock_tool(**kwargs):
            return {"status": "success", "inputs": kwargs, "call_count": 1}
        
        registry.register("mock", mock_tool)
        return registry

    @pytest.mark.asyncio
    async def test_single_task_execution(self, registry: ToolRegistry):
        """Test executing a pipeline with a single task."""
        pipeline = Pipeline(
            id="single_task_pipeline",
            goal="Test single task execution",
            tasks=[
                Task(
                    id="task_one",
                    tool="mock",
                    inputs={"input_data": "test_value"}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        assert "task_one" in result.outputs
        output = result.outputs["task_one"]
        assert output["status"] == "success"
        assert output["inputs"]["input_data"] == "test_value"

    @pytest.mark.asyncio
    async def test_multiple_independent_tasks(self, registry: ToolRegistry):
        """Test executing multiple independent tasks (no dependencies)."""
        pipeline = Pipeline(
            id="independent_tasks",
            goal="Test multiple independent tasks",
            tasks=[
                Task(
                    id="task_alpha",
                    tool="mock",
                    inputs={"input_data": "alpha"}
                ),
                Task(
                    id="task_beta",
                    tool="mock",
                    inputs={"input_data": "beta"}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        assert "task_alpha" in result.outputs
        assert "task_beta" in result.outputs
        assert result.outputs["task_alpha"]["inputs"]["input_data"] == "alpha"
        assert result.outputs["task_beta"]["inputs"]["input_data"] == "beta"

    @pytest.mark.asyncio
    async def test_task_with_literal_inputs(self, registry: ToolRegistry):
        """Test task execution with various literal input types."""
        pipeline = Pipeline(
            id="literal_inputs",
            goal="Test literal inputs",
            tasks=[
                Task(
                    id="task_literals",
                    tool="mock",
                    inputs={
                        "string_val": "hello",
                        "number_val": 42,
                        "bool_val": True,
                        "list_val": [1, 2, 3],
                        "dict_val": {"key": "value"}
                    }
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        output = result.outputs["task_literals"]
        assert output["inputs"]["string_val"] == "hello"
        assert output["inputs"]["number_val"] == 42
        assert output["inputs"]["bool_val"] is True
        assert output["inputs"]["list_val"] == [1, 2, 3]
        assert output["inputs"]["dict_val"] == {"key": "value"}


class TestDAGExecutorDependencies:
    """Test dependency resolution and execution ordering."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create tool registry with mock tool."""
        registry = ToolRegistry()
        
        async def mock_tool(**kwargs):
            return {"status": "success", "message": "Mock tool executed", "inputs": kwargs}
        
        registry.register("mock", mock_tool)
        return registry

    @pytest.mark.asyncio
    async def test_linear_dependency_chain(self, registry: ToolRegistry):
        """Test linear task dependency chain (task1 -> task2 -> task3)."""
        pipeline = Pipeline(
            id="linear_chain",
            goal="Test linear dependency chain",
            tasks=[
                Task(
                    id="step_one",
                    tool="mock",
                    inputs={"input_data": "start"}
                ),
                Task(
                    id="step_two",
                    tool="mock",
                    inputs={"input_data": "{{step_one.output.message}}"}
                ),
                Task(
                    id="step_three",
                    tool="mock",
                    inputs={"input_data": "{{step_two.output.message}}"}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        # All tasks should complete
        assert "step_one" in result.outputs
        assert "step_two" in result.outputs
        assert "step_three" in result.outputs

        # Template resolution should work
        assert result.outputs["step_two"]["inputs"]["input_data"] == "Mock tool executed"
        assert result.outputs["step_three"]["inputs"]["input_data"] == "Mock tool executed"

    @pytest.mark.asyncio
    async def test_multiple_inputs_from_previous_tasks(self, registry: ToolRegistry):
        """Test task receiving inputs from multiple upstream tasks."""
        pipeline = Pipeline(
            id="multi_input",
            goal="Test multiple inputs",
            tasks=[
                Task(
                    id="source_one",
                    tool="mock",
                    inputs={"input_data": "first"}
                ),
                Task(
                    id="source_two",
                    tool="mock",
                    inputs={"input_data": "second"}
                ),
                Task(
                    id="combiner",
                    tool="mock",
                    inputs={
                        "first_input": "{{source_one.output.message}}",
                        "second_input": "{{source_two.output.message}}"
                    }
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        assert result.outputs["combiner"]["inputs"]["first_input"] == "Mock tool executed"
        assert result.outputs["combiner"]["inputs"]["second_input"] == "Mock tool executed"


class TestDAGExecutorParallelOver:
    """Test parallel_over (fan-out) functionality."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create tool registry with mock tool."""
        registry = ToolRegistry()
        
        async def mock_tool(**kwargs):
            return {"status": "success", "inputs": kwargs}
        
        registry.register("mock", mock_tool)
        return registry

    @pytest.mark.asyncio
    async def test_parallel_over_basic(self, registry: ToolRegistry):
        """Test basic parallel_over execution."""
        pipeline = Pipeline(
            id="parallel_test",
            goal="Test parallel over",
            tasks=[
                Task(
                    id="list_source",
                    tool="mock",
                    inputs={"input_data": "items"}
                ),
                Task(
                    id="parallel_task",
                    tool="mock",
                    parallel_over="{{list_source.output}}",
                    inputs={"current_item": "{{item}}"}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        # parallel_task output should be a list
        assert isinstance(result.outputs["parallel_task"], list)
        
        # Each output should be a successful execution
        for output in result.outputs["parallel_task"]:
            assert output["status"] == "success"

    @pytest.mark.asyncio
    async def test_parallel_over_with_item_binding(self, registry: ToolRegistry):
        """Test that {{item}} is properly bound in parallel_over."""
        pipeline = Pipeline(
            id="item_binding_test",
            goal="Test item binding",
            tasks=[
                Task(
                    id="prepare_items",
                    tool="mock",
                    inputs={"input_data": "test"}
                ),
                Task(
                    id="process_items",
                    tool="mock",
                    parallel_over="{{prepare_items.output}}",
                    inputs={"item_key": "{{item}}"}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        outputs = result.outputs["process_items"]
        # The mock tool returns a dict with keys: status, inputs
        # (call_count and message appear only to exist in one path)
        # When used as parallel_over, it iterates over dict keys
        assert len(outputs) >= 2  # at least 2 keys
        
        # Each execution should have received a different key
        items_received = [out["inputs"]["item_key"] for out in outputs]
        assert len(set(items_received)) == len(outputs)  # All items should be unique


class TestDAGExecutorContext:
    """Test resolution context management."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create tool registry with mock tool."""
        registry = ToolRegistry()
        
        async def mock_tool(**kwargs):
            return {"status": "success", "inputs": kwargs}
        
        registry.register("mock", mock_tool)
        return registry

    @pytest.mark.asyncio
    async def test_pipeline_goal_in_context(self, registry: ToolRegistry):
        """Test that pipeline goal is available in resolution context."""
        pipeline = Pipeline(
            id="goal_test",
            goal="This is the pipeline goal",
            tasks=[
                Task(
                    id="read_goal",
                    tool="mock",
                    inputs={"goal_value": "{{pipeline.goal}}"}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        assert result.outputs["read_goal"]["inputs"]["goal_value"] == "This is the pipeline goal"

    @pytest.mark.asyncio
    async def test_pipeline_inputs(self, registry: ToolRegistry):
        """Test that pipeline inputs are accessible via template."""
        pipeline = Pipeline(
            id="pipeline_inputs_test",
            goal="Test pipeline inputs",
            inputs={"param1": "value1", "param2": "value2"},
            tasks=[
                Task(
                    id="use_inputs",
                    tool="mock",
                    inputs={
                        "input_one": "{{pipeline.inputs.param1}}",
                        "input_two": "{{pipeline.inputs.param2}}"
                    }
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        assert result.outputs["use_inputs"]["inputs"]["input_one"] == "value1"
        assert result.outputs["use_inputs"]["inputs"]["input_two"] == "value2"

    @pytest.mark.asyncio
    async def test_session_context(self, registry: ToolRegistry):
        """Test that session values are accessible in resolution context."""
        pipeline = Pipeline(
            id="session_test",
            goal="Test session",
            tasks=[
                Task(
                    id="read_session",
                    tool="mock",
                    inputs={"session_value": "{{session.seed_key}}"}
                )
            ]
        )

        context = ResolutionContext(
            pipeline_inputs=pipeline.inputs, 
            pipeline_goal=pipeline.goal,
            session={"seed_key": "seed_value"}
        )
        result = await execute_pipeline(pipeline, registry, context)

        assert result.outputs["read_session"]["inputs"]["session_value"] == "seed_value"


class TestDAGExecutorRetry:
    """Test retry mechanism."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create tool registry."""
        return ToolRegistry()

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_failures(self, registry: ToolRegistry):
        """Test that retry succeeds after initial failures."""
        attempt_count = {"value": 0}
        
        async def flaky_tool(**kwargs):
            attempt_count["value"] += 1
            if attempt_count["value"] < 3:
                raise Exception(f"Attempt {attempt_count['value']} failed")
            return {"status": "success", "message": "Third attempt worked"}
        
        registry.register("flaky_tool", flaky_tool)

        pipeline = Pipeline(
            id="retry_test",
            goal="Test retry",
            tasks=[
                Task(
                    id="flaky_task",
                    tool="flaky_tool",
                    retry=2,
                    inputs={}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        assert attempt_count["value"] == 3
        assert result.outputs["flaky_task"]["status"] == "success"
        assert result.outputs["flaky_task"]["message"] == "Third attempt worked"

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self, registry: ToolRegistry):
        """Test that tool is not retried if it succeeds immediately."""
        call_count = {"value": 0}
        
        async def reliable_tool(**kwargs):
            call_count["value"] += 1
            return {"status": "success"}
        
        registry.register("reliable_tool", reliable_tool)

        pipeline = Pipeline(
            id="no_retry_test",
            goal="Test no retry on success",
            tasks=[
                Task(
                    id="reliable_task",
                    tool="reliable_tool",
                    retry=3,
                    inputs={}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        # Should only be called once
        assert call_count["value"] == 1

    @pytest.mark.asyncio
    async def test_failure_after_retries_exhausted(self, registry: ToolRegistry):
        """Test that error is raised when retries are exhausted."""
        
        async def permanent_failure(**kwargs):
            raise Exception("Always fails")
        
        registry.register("permanent_failure", permanent_failure)

        pipeline = Pipeline(
            id="exhausted_retries",
            goal="Test exhausted retries",
            tasks=[
                Task(
                    id="persistent_failure",
                    tool="permanent_failure",
                    retry=2,
                    inputs={}
                )
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        
        with pytest.raises(TaskError) as exc_info:
            await execute_pipeline(pipeline, registry, context)

        assert exc_info.value.task_id == "persistent_failure"
        assert exc_info.value.attempt == 3


class TestDAGExecutorErrors:
    """Test error handling and edge cases."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create tool registry with mock tool."""
        registry = ToolRegistry()
        
        async def mock_tool(**kwargs):
            return {"status": "success"}
        
        registry.register("mock", mock_tool)
        return registry

    @pytest.mark.asyncio
    async def test_tool_not_found_in_registry(self, registry: ToolRegistry):
        """Test error when tool is not found in registry."""
        pipeline = Pipeline(
            id="missing_tool",
            goal="Test missing tool",
            tasks=[
                Task(
                    id="task_with_missing_tool",
                    tool="mock",
                    inputs={}
                )
            ]
        )

        # Remove the tool
        registry._tools.clear()

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        
        with pytest.raises(TaskError) as exc_info:
            await execute_pipeline(pipeline, registry, context)

        assert exc_info.value.task_id == "task_with_missing_tool"

    def test_empty_pipeline_not_allowed(self):
        """Test that empty task list is rejected."""
        with pytest.raises(Exception):  # Pydantic validation error
            Pipeline(
                id="empty",
                goal="Empty pipeline",
                tasks=[]
            )


class TestDAGExecutorTopology:
    """Test DAG topology and execution ordering."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create tool registry."""
        return ToolRegistry()

    @pytest.mark.asyncio
    async def test_topological_sort_order(self, registry: ToolRegistry):
        """Test that tasks are executed in correct topological order."""
        execution_order = []

        async def make_tracking_tool(task_name: str):
            async def tracking_tool(**kwargs):
                execution_order.append(task_name)
                return {"name": task_name}
            return tracking_tool

        # Register tracking tools
        for i in range(1, 5):
            task_name = f"task_{i}"
            registry.register(f"tracker_{i}", await make_tracking_tool(task_name))

        pipeline = Pipeline(
            id="order_test",
            goal="Test execution order",
            tasks=[
                Task(id="task_4", tool="tracker_4", inputs={"dep": "{{task_2.output}}"}),
                Task(id="task_2", tool="tracker_2", inputs={"dep": "{{task_1.output}}"}),
                Task(id="task_3", tool="tracker_3", inputs={"dep": "{{task_1.output}}"}),
                Task(id="task_1", tool="tracker_1", inputs={})
            ]
        )

        context = ResolutionContext(pipeline_inputs=pipeline.inputs, pipeline_goal=pipeline.goal)
        result = await execute_pipeline(pipeline, registry, context)

        # task_1 must be before task_2 and task_3
        assert execution_order.index("task_1") < execution_order.index("task_2")
        assert execution_order.index("task_1") < execution_order.index("task_3")
        # task_2 must be before task_4
        assert execution_order.index("task_2") < execution_order.index("task_4")
        # All tasks should be executed
        assert all(task in execution_order for task in ["task_1", "task_2", "task_3", "task_4"])


