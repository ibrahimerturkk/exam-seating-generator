import exam_seating_generator


def test_import() -> None:
    """Test that the package can be imported without errors."""
    assert isinstance(exam_seating_generator.__name__, str)
