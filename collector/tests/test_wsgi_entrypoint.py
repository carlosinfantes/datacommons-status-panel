def test_gunicorn_target_is_a_callable_that_defers_configuration():
    # Importing must not require any DCS_* variable: the app configures itself on
    # the first request, so a misconfigured revision fails a request, not startup.
    from dc_status.app import application

    assert callable(application)
