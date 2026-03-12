"""Tests for user extension tools."""

import os
import pytest

import freecad_ai.config as config_mod


class TestUserToolsConfig:
    def test_user_tools_dir_exists(self):
        """USER_TOOLS_DIR is defined in config module."""
        assert hasattr(config_mod, "USER_TOOLS_DIR")
        assert "tools" in config_mod.USER_TOOLS_DIR

    def test_config_has_user_tools_fields(self):
        """AppConfig has user tool config fields."""
        cfg = config_mod.AppConfig()
        assert cfg.user_tools_disabled == []
        assert cfg.scan_freecad_macros is False

    def test_config_roundtrip(self, tmp_config_dir):
        """User tools config survives save/load."""
        cfg = config_mod.AppConfig()
        cfg.user_tools_disabled = ["bad_tool.py"]
        cfg.scan_freecad_macros = True
        config_mod.save_config(cfg)
        loaded = config_mod.load_config()
        assert loaded.user_tools_disabled == ["bad_tool.py"]
        assert loaded.scan_freecad_macros is True


class TestAstValidation:
    def test_valid_file(self, tmp_path):
        """Valid file with typed function passes validation."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "good.py"
        f.write_text(
            'def my_tool(diameter: float, count: int = 8) -> str:\n'
            '    """Create something."""\n'
            '    return "done"\n'
        )
        result = validate_file(str(f))
        assert result.valid
        assert len(result.functions) == 1
        assert result.functions[0].name == "my_tool"
        assert len(result.functions[0].params) == 2
        assert not result.warnings

    def test_syntax_error(self, tmp_path):
        """File with syntax error fails validation."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "bad.py"
        f.write_text("def oops(:\n")
        result = validate_file(str(f))
        assert not result.valid
        assert "syntax" in result.error.lower()

    def test_no_typed_functions(self, tmp_path):
        """File with no typed functions fails validation."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "untyped.py"
        f.write_text("def foo(x, y):\n    return x + y\n")
        result = validate_file(str(f))
        assert not result.valid
        assert "no valid tool functions" in result.error.lower()

    def test_private_functions_skipped(self, tmp_path):
        """Functions starting with _ are skipped."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "private.py"
        f.write_text(
            'def _helper(x: int) -> int:\n    return x\n'
            'def public_tool(x: int) -> str:\n'
            '    """Do stuff."""\n'
            '    return str(x)\n'
        )
        result = validate_file(str(f))
        assert result.valid
        assert len(result.functions) == 1
        assert result.functions[0].name == "public_tool"

    def test_unsupported_param_type(self, tmp_path):
        """Unsupported param types produce warnings."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "weird.py"
        f.write_text(
            'def my_tool(data: dict) -> str:\n'
            '    """Process data."""\n'
            '    return "ok"\n'
        )
        result = validate_file(str(f))
        assert not result.valid or len(result.warnings) > 0

    def test_missing_docstring_warns(self, tmp_path):
        """Missing docstring produces a warning."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "nodoc.py"
        f.write_text("def my_tool(x: float) -> str:\n    return 'ok'\n")
        result = validate_file(str(f))
        assert result.valid
        assert any("docstring" in w.lower() for w in result.warnings)

    def test_multiple_functions(self, tmp_path):
        """File with multiple valid functions extracts all."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "multi.py"
        f.write_text(
            'def tool_a(x: float) -> str:\n    """A."""\n    return "a"\n\n'
            'def tool_b(y: int, z: str = "hi") -> str:\n    """B."""\n    return "b"\n'
        )
        result = validate_file(str(f))
        assert result.valid
        assert len(result.functions) == 2

    def test_fcmacro_extension(self, tmp_path):
        """FCMacro files are accepted."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "macro.FCMacro"
        f.write_text(
            'def make_thing(size: float = 10.0) -> str:\n'
            '    """Make a thing."""\n'
            '    return "made"\n'
        )
        result = validate_file(str(f))
        assert result.valid

    def test_default_values_extracted(self, tmp_path):
        """Default values are correctly extracted."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "defaults.py"
        f.write_text(
            'def my_tool(x: float, y: int = 5, name: str = "hi", flag: bool = True) -> str:\n'
            '    """Tool."""\n'
            '    return "ok"\n'
        )
        result = validate_file(str(f))
        func = result.functions[0]
        assert func.params[0].required is True   # x
        assert func.params[1].required is False   # y
        assert func.params[1].default == 5
        assert func.params[2].default == "hi"
        assert func.params[3].default is True


from freecad_ai.tools.registry import ToolRegistry


class TestLoadAndRegister:
    def _write_tool(self, tmp_path, name="my_tool.py", content=None):
        if content is None:
            content = (
                'def greet(name: str, times: int = 1) -> str:\n'
                '    """Greet someone."""\n'
                '    return f"Hello {name}! " * times\n'
            )
        f = tmp_path / name
        f.write_text(content)
        return f

    def test_load_module(self, tmp_path):
        """load_user_tools loads functions from a file."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tools = load_user_tools(str(tmp_path))
        assert len(tools) == 1
        assert tools[0].name == "user_greet"

    def test_tool_execution(self, tmp_path):
        """Loaded tool can be executed and returns ToolResult."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tools = load_user_tools(str(tmp_path))
        result = tools[0].handler(name="World", times=2)
        assert result.success
        assert "Hello World" in result.output

    def test_tool_exception_handling(self, tmp_path):
        """Tool that raises returns error ToolResult."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, content=(
            'def boom(x: int) -> str:\n'
            '    """Explode."""\n'
            '    raise ValueError("kaboom")\n'
        ))
        tools = load_user_tools(str(tmp_path))
        result = tools[0].handler(x=1)
        assert not result.success
        assert "kaboom" in result.error

    def test_dict_return(self, tmp_path):
        """Tool returning dict maps to ToolResult fields."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, content=(
            'def info(x: int) -> dict:\n'
            '    """Get info."""\n'
            '    return {"output": "done", "data": {"val": x}}\n'
        ))
        tools = load_user_tools(str(tmp_path))
        result = tools[0].handler(x=42)
        assert result.success
        assert result.output == "done"
        assert result.data == {"val": 42}

    def test_register_into_registry(self, tmp_path):
        """User tools register into ToolRegistry."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tools = load_user_tools(str(tmp_path))
        registry = ToolRegistry()
        for t in tools:
            registry.register(t)
        assert registry.get("user_greet") is not None
        result = registry.execute("user_greet", {"name": "Test"})
        assert result.success

    def test_disabled_files_skipped(self, tmp_path):
        """Disabled files are not loaded."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, "skip_me.py")
        tools = load_user_tools(str(tmp_path), disabled=["skip_me.py"])
        assert len(tools) == 0

    def test_params_schema(self, tmp_path):
        """Loaded tool has correct ToolParam schema."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tool = load_user_tools(str(tmp_path))[0]
        params = {p.name: p for p in tool.parameters}
        assert params["name"].type == "string"
        assert params["name"].required is True
        assert params["times"].type == "integer"
        assert params["times"].required is False
        assert params["times"].default == 1

    def test_multiple_files(self, tmp_path):
        """Multiple files in directory all get loaded."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, "tool_a.py",
            'def alpha(x: float) -> str:\n    """A."""\n    return "a"\n')
        self._write_tool(tmp_path, "tool_b.py",
            'def beta(y: int) -> str:\n    """B."""\n    return "b"\n')
        tools = load_user_tools(str(tmp_path))
        names = {t.name for t in tools}
        assert names == {"user_alpha", "user_beta"}


class TestSetupIntegration:
    def test_create_default_registry_includes_user_tools(self, tmp_path, monkeypatch):
        """create_default_registry loads user tools."""
        import freecad_ai.config as config_mod
        monkeypatch.setattr(config_mod, "USER_TOOLS_DIR", str(tmp_path))

        (tmp_path / "my_tool.py").write_text(
            'def hello(name: str) -> str:\n'
            '    """Say hello."""\n'
            '    return f"Hi {name}"\n'
        )

        from freecad_ai.tools.setup import create_default_registry
        registry = create_default_registry(include_mcp=False)
        assert registry.get("user_hello") is not None


class TestScanMacros:
    def test_extra_dirs_scanned(self, tmp_path):
        """load_user_tools accepts extra_dirs to scan."""
        from freecad_ai.extensions.user_tools import load_user_tools

        macros_dir = tmp_path / "macros"
        macros_dir.mkdir()
        (macros_dir / "macro_tool.FCMacro").write_text(
            'def macro_fn(r: float) -> str:\n'
            '    """A macro."""\n'
            '    return "ok"\n'
        )

        tools = load_user_tools(str(tmp_path / "empty"), extra_dirs=[str(macros_dir)])
        assert len(tools) == 1
        assert tools[0].name == "user_macro_fn"

    def test_primary_dir_takes_precedence(self, tmp_path):
        """Primary dir tools override extra_dirs with same name."""
        from freecad_ai.extensions.user_tools import load_user_tools

        primary = tmp_path / "primary"
        primary.mkdir()
        (primary / "tool.py").write_text(
            'def shared(x: int) -> str:\n    """Primary."""\n    return "primary"\n'
        )

        extra = tmp_path / "extra"
        extra.mkdir()
        (extra / "tool2.py").write_text(
            'def shared(x: int) -> str:\n    """Extra."""\n    return "extra"\n'
        )

        tools = load_user_tools(str(primary), extra_dirs=[str(extra)])
        assert len(tools) == 1
        result = tools[0].handler(x=1)
        assert result.output == "primary"
