from typing import Optional, Self, Literal, Union, Any
from pathlib import Path
from pydantic import BaseModel, Field
from abc import ABC, abstractmethod


class Tool(ABC, BaseModel):
    """Base class for all tools"""
    
    @abstractmethod
    def __call__(self):
        raise NotImplementedError("The tool is not implemented")
    
    @abstractmethod
    def __name__(self) -> str:
        raise NotImplementedError("The tool is not implemented")
    
    
class FileManagementTool(Tool):
    """Manages files and folders"""
    name: str
    type_of_action: Literal["create", "delete", "read"] = Field(..., description="Type of action to perform")
    type_of_item: Literal["folder", "file"] = Field(..., description="Type of item to perform action on")
    parent_folder_path: Path = Field(..., description="Parent folder path")
    
    def __call__(self) -> Any:
        if self.type_of_action == "create":
            return self.create()
        elif self.type_of_action == "delete":
            return self.delete()
        elif self.type_of_action == "read":
            return self.read()
        else:
            raise Exception("Unknown type of action")
    
    def create(self):
        if self.type_of_item == "folder":
            self.create_folder()
        elif self.type_of_item == "file":
            self.create_file()
        else:
            raise Exception("Unknown type of item")
    
    def delete(self):
        if self.type_of_item == "folder":
            self.delete_folder()
        elif self.type_of_item == "file":
            self.delete_file()
        else:
            raise Exception("Unknown type of item")
    
    def read(self):
        if self.type_of_item == "folder":
            return self.read_folder()
        elif self.type_of_item == "file":
            return self.read_file()
        else:
            raise Exception("Unknown type of item")
    
    def create_folder(self):
        folder_path = self.parent_folder_path / self.name
        folder_path.mkdir(parents=True, exist_ok=True)
        print(f"The folder with name `{self.name}` is created in the path {self.parent_folder_path}")
    
    def create_file(self):
        file_path = self.parent_folder_path / self.name
        file_path.touch(exist_ok=True)
        print(f"The file with name `{self.name}` is created in the path {self.parent_folder_path}")
    
    def delete_folder(self):
        folder_path = self.parent_folder_path / self.name
        if folder_path.is_dir():
            folder_path.rmdir()
            print(f"The folder with name `{self.name}` is deleted from the path {self.parent_folder_path}")
        else:
            print(f"The folder with name `{self.name}` does not exist in the path {self.parent_folder_path}")
    
    def delete_file(self):
        file_path = self.parent_folder_path / self.name
        if file_path.is_file():
            file_path.unlink()
            print(f"The file with name `{self.name}` is deleted from the path {self.parent_folder_path}")
        else:
            print(f"The file with name `{self.name}` does not exist in the path {self.parent_folder_path}")
    
    def read_folder(self):
        folder_path = self.parent_folder_path / self.name
        if folder_path.is_dir():
            return list(folder_path.iterdir())
        else:
            print(f"The folder with name `{self.name}` does not exist in the path {self.parent_folder_path}")
            return []
    
    def read_file(self):
        file_path = self.parent_folder_path / self.name
        if file_path.is_file():
            return file_path.read_text()
        else:
            print(f"The file with name `{self.name}` does not exist in the path {self.parent_folder_path}")
            return ""
    
    @property
    def __name__(self) -> str:
        return "FileManagementTool"


# class FolderCreator(Tool):
#     """Creates folder"""
#     name: str
#     parent_folder_path: Path = Field(..., description="Parent folder path")
    
#     def __call__(self):
#         print(f"The folder with name `{self.name}` is created in the path {self.parent_folder_path}")
      
#     @property  
#     def __name__(self) -> str:
#         return "FolderCreator"


# class FileCreator(Tool):
#     """Creates file"""
#     name: str
#     parent_folder_path: Path = Field(..., description="Parent folder path")
    
#     def __call__(self):
#         print(f"The file with name `{self.name}` is created in the path {self.parent_folder_path}")
       
#     @property 
#     def __name__(self) -> str:
#         return "FileCreator"


# class UnknownTool(Tool):
#     """Unknown tool if not sure what to pick"""
    
#     @property
#     def __name__(self) -> str:
#         return "UnknownTool"
    
#     def __call__(self):
#         raise Exception("Cannot call the tool since unknown type")
       
