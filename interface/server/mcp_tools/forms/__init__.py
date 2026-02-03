"""Forms tools for structured data collection."""

# Import to trigger registration
from . import define as define_module
from . import show as show_module
from . import save as save_module
from . import list_submissions as list_module

# Re-export for direct access
from .define import forms_define
from .show import forms_show
from .save import forms_save
from .list_submissions import forms_list

__all__ = [
    "forms_define",
    "forms_show",
    "forms_save",
    "forms_list",
]
