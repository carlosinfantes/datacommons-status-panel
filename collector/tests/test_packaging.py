import dc_status


def test_package_exposes_version():
    assert isinstance(dc_status.__version__, str)
    assert dc_status.__version__.count(".") == 2
