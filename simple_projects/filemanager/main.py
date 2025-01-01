import sys
import os
from pathlib import Path
from typing import Callable, Dict, Optional, Union
import nest_asyncio
import logging
import dotenv
from dataclasses import dataclass

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
from models import FileManagementTool
from utilities import generate_directory_structure

model = VertexAIModel(
    model_name=os.getenv("GEMINI_MODEL"),
    project_id=os.getenv("VERTEX_AI_PROJECT_ID"),
)

class FileManagerDeps(BaseModel):  
    pwd: Path = Field(..., description="Path of the working directory. This is always the parent directory of the folders and files.")

system_prompt = "You are a file manager agent. Your task is to manage files and folders within the specified directory. You can create, delete, and read files and folders. You will be provided with path of working directory. Please ensure that all operations are performed within this directory or within subdirectories of this directory. If you encounter any errors, provide a clear and concise error message."
filemanager = Agent(
    model, result_type=FileManagementTool, retries=3,
    deps_type=FileManagerDeps, system_prompt=system_prompt
)



@filemanager.system_prompt  
async def add_cwd_and_folder_structure(ctx: RunContext[FileManagerDeps]) -> str:
    directory_structure = generate_directory_structure(ctx.deps.pwd)
    return (
        f"The current working directory is: `{ctx.deps.pwd}`. "
        "Remember at all costs, you should not perform any operations outside this directory. "
        f"Here is the directory structure within current working directory:\n{directory_structure}\n"
    )


@filemanager.result_validator
async def validate_parent_folder_path(ctx: RunContext[FileManagerDeps], final_response: FileManagementTool) -> FileManagementTool:
    """checks if the parent folder path is within the working directory or working directory itself"""
    if not final_response.parent_folder_path.is_dir():
        raise ModelRetry(f"Parent folder path does not exist: {final_response.parent_folder_path}") 
    
    if not final_response.parent_folder_path.is_absolute():
        raise ModelRetry(f"Parent folder path is not absolute: {final_response.parent_folder_path}")
    
    if not final_response.parent_folder_path.is_relative_to(ctx.deps.pwd):
        raise ModelRetry(f"Parent folder path is not within the working directory: {final_response.parent_folder_path}")
    
    return final_response


dependencies = FileManagerDeps(
    pwd=Path(r"/Users/santokalayil/Developer/projects/AI_AGENTS/simple_projects/filemanager/TEMP"),
)

result = filemanager.run_sync(
    "create another folder with name sajan in the working directory", 
    message_history=[],
    deps=dependencies
)
actual_result = result.data
actual_result
actual_result()



result = filemanager.run_sync(
    "create a folder with name temporary in the 'sajan' directory", 
    message_history=[],
    deps=dependencies
)
actual_result = result.data
actual_result
actual_result()

result = filemanager.run_sync(
    "create file in temporary folder with name test.txt", 
    message_history=[],
    deps=dependencies
)
actual_result = result.data
actual_result
actual_result()

result.cost()
result.all_messages()

# call function
actual_result()







# print(result.data)
# print(result.all_messages())

# print(100 * "-")


# result = agent.run_sync('who is your favourite cricketer?', message_history=result.all_messages())
# print(result.data)
# print(result.all_messages())
# print(result.new_messages())