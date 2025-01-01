from abc import ABC, abstractmethod
from pydantic import BaseModel, Field, model_validator, field_validator
from typing import Any, Self

# global_vars = {}
# local_vars = {}

# # function definition into local vars
# code = """
# def print_hello():
#     print('hello world')
#     return "Success"
# """
# exec(code, global_vars, local_vars)

# # to evaludate function
# eval("print_hello()", global_vars, local_vars)

class Tool(ABC, BaseModel):
    """Base class for all tools"""
    
    @abstractmethod
    def __call__(self):
        raise NotImplementedError("The tool is not implemented")
    
    @abstractmethod
    def __name__(self) -> str:
        raise NotImplementedError("The tool is not implemented")
    
    
    
class Code(Tool):
    """Python Code related object used by/for the coder agent"""
    # code: str = Field(..., description="Python code to be executed")
    # description: str = Field("", description="Description of the code")
    function: str = Field("", description="Function Body to be defined. This will be used to define the function")
    function_call_string: str = Field("", description="Function call string. This will be used to call the function")
    
    # pydantic validation method to run the code and raise error if any and return the output
    @field_validator('function', 'function_call_string', mode='before')
    @classmethod
    def escape_slash_removal(cls, value: str) -> str:
        return value.replace("\\n", "\n").replace(r'\"', r'"')
    
    @model_validator(mode='after')
    def execute_code_n_check(self) -> Self:
        print(100 * "=")
        print("Executing code")
        print(self.function)
        print(self.function_call_string)
        print(100 * "*")
        return self
    
    def __call__(self) -> Any:
        ...
        # return self.execute_code()
    
    def __name__(self) -> str:
        return "Code"
    
    
    # def execute_code(self):
    #     try:
    #         exec(self.code)
    #     except Exception as e:
    #         return str(e)
    #     return self.output

