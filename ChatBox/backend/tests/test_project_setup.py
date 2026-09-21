def test_project_package_is_importable() -> None:
    import app

    assert app is not None
