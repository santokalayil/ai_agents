import sys
import os
from pathlib import Path
from typing import Callable, Dict, Optional, Union, Any
import nest_asyncio
import logging
import dotenv
from dataclasses import dataclass
import traceback
import asyncio

# setting up jupyter style runs and sys path to access libs from folder
dotenv.load_dotenv(".../.env")
sys.path.append(str(Path(__file__).parent.parent.parent))
nest_asyncio.apply()
logging.basicConfig(level=logging.DEBUG)

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, Tool, ModelRetry
from pydantic_ai.result import RunResult
from pydantic_ai.models.vertexai import VertexAIModel

# project specific module imports
from models import Code
# from utilities import generate_directory_structure

model = VertexAIModel(
    model_name=os.getenv("GEMINI_MODEL"),
    project_id=os.getenv("VERTEX_AI_PROJECT_ID"),
)

class PythonInterpreterDeps(BaseModel):  
    global_vars: Dict[str, Any] = Field(..., description="Global variables to be used in the code")
    local_vars: Dict[str, Any] = Field(..., description="Local variables to be used in the code")


system_prompt = (
    "You are a coding agent. Your task is to write python code for the given task. "
    "You will be provided with the task description and the context. "
    "You need to write the code to perform the task. "
    # "If you encounter any errors, provide a clear and concise error message while raising the error."
    "NEVER ever FORGET to add type hints to your code. It is very important. "
)
coder = Agent(
    model, result_type=Code, retries=3,
    deps_type=PythonInterpreterDeps, 
    system_prompt=system_prompt
)


@coder.result_validator
async def execute_function_to_check(ctx: RunContext[Code], final_response: Code) -> Code:
    """checks if function executes properly"""
    
    try:
        print("Executing function".center(100, "-"))
        exec(final_response.function, ctx.deps.global_vars, ctx.deps.local_vars)
        out = eval(final_response.function_call_string, ctx.deps.global_vars, ctx.deps.local_vars)
        if out:
            print(f"function returned the output: {out}")
        print("Function executed successfully".center(100, "-"))
    except Exception as e:
        raise ModelRetry(f"Error while executing the function: {traceback.format_exc()}")
    
    return final_response




async def generate_output(query: str) -> Code:
    dependencies = PythonInterpreterDeps(global_vars={}, local_vars={})
    result = await coder.run(
        query, 
        message_history=[],
        deps=dependencies
    )
    actual_result = result.data
    print("COST".center(100, "="))
    print(result.cost())
    print("MESSAGES".center(100, "="))
    print(result.all_messages())
    print(100 * "*")
    return actual_result


if __name__ == "__main__":
    while True:
        # "write a function to calulate weighting based in marketcap of index constituent securities. Total numeber of securities is 15. "
        result = asyncio.run(generate_output(input("Enter the logic to start coding: ")))
        out = f"\n\n{result.function}\n\n{result.function_call_string}\n\n\n"
        
        print("Generated code".center(100, "-"))
        print(out)
        print(100 * "=")
