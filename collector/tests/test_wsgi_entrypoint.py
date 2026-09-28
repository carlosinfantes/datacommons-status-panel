# Copyright 2026 Carlos Infantes
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

def test_gunicorn_target_is_a_callable_that_defers_configuration():
    # Importing must not require any DCS_* variable: the app configures itself on
    # the first request, so a misconfigured revision fails a request, not startup.
    from dc_status.app import application

    assert callable(application)
