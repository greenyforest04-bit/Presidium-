def __getattr__(name):
    import importlib
    for modname in ("tokens", "animations", "theme_engine"):
        module = importlib.import_module(f"presidium.ui.design_system.{modname}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TOKENS", "SPACE", "TYPE", "SHADOW", "RADIUS",
    "Colors", "LightColors", "Spacing", "Typography", "Shadows", "Radius",
    "fade_in", "slide_up", "spring_scale", "shimmer", "press_button", "AnimationGroup",
    "ThemeEngine", "ThemeMode", "theme_engine",
]
