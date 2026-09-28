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


from dc_status import __version__
from dc_status.build import build_info


def test_a_release_image_reports_its_tag_commit_and_source():
    info = build_info(
        {
            "DCS_BUILD_VERSION": "1.0.0-rc.6",
            "DCS_BUILD_COMMIT": "b630f5d6c9eba9ca4d848db7ac2a4903965042b6",
            "DCS_BUILD_SOURCE": "https://github.com/example/dc-status",
        }
    )
    assert info == {
        "version": "1.0.0-rc.6",
        "commit": "b630f5d6c9eb",
        "source": "https://github.com/example/dc-status",
    }


def test_a_local_run_falls_back_to_the_package_version():
    assert build_info({}) == {"version": __version__, "commit": None, "source": None}
