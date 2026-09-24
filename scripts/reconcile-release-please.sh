#!/usr/bin/env bash
set -euo pipefail

: "${CONFIG_FILE:?CONFIG_FILE is required}"
: "${MANIFEST_FILE:?MANIFEST_FILE is required}"

first_paths="${FIRST_PATHS:-}"
first_outputs="${FIRST_OUTPUTS:-}"
retry_paths="${RETRY_PATHS:-}"
retry_outputs="${RETRY_OUTPUTS:-}"
before_releases="${BEFORE_RELEASES:-}"
after_releases="${AFTER_RELEASES:-}"
release_sha="${RELEASE_SHA:-}"
replay_tag="${REPLAY_TAG:-}"

[ -n "$first_paths" ] || first_paths='[]'
[ -n "$first_outputs" ] || first_outputs='{}'
[ -n "$retry_paths" ] || retry_paths='[]'
[ -n "$retry_outputs" ] || retry_outputs='{}'
[ -n "$before_releases" ] || before_releases='[]'
[ -n "$after_releases" ] || after_releases='[]'

jq -cn \
  --slurpfile config "$CONFIG_FILE" \
  --slurpfile manifest "$MANIFEST_FILE" \
  --argjson first_paths "$first_paths" \
  --argjson first_outputs "$first_outputs" \
  --argjson retry_paths "$retry_paths" \
  --argjson retry_outputs "$retry_outputs" \
  --argjson before_releases "$before_releases" \
  --argjson after_releases "$after_releases" \
  --arg release_sha "$release_sha" \
  --arg replay_tag "$replay_tag" '
    def configured($package; $name; $default):
      if $package | has($name) then $package[$name]
      elif $config[0] | has($name) then $config[0][$name]
      else $default
      end;

    def normalized($paths; $outputs):
      [
        $paths[] as $path
        | (if $path == "." then "" else ($path + "--") end) as $prefix
        | {
            path: $path,
            component: ($config[0].packages[$path].component // $path),
            tag_name: $outputs[$prefix + "tag_name"],
            version: $outputs[$prefix + "version"],
            sha: $outputs[$prefix + "sha"]
          }
        | select(.tag_name != null and .version != null and .sha != null)
      ];

    def expected_releases:
      [
        $manifest[0] | to_entries[]
        | .key as $path
        | .value as $version
        | ($config[0].packages[$path] // {}) as $package
        | ($package.component // $path) as $component
        | configured($package; "include-v-in-tag"; true) as $include_v
        | configured($package; "include-component-in-tag"; true) as $include_component
        | configured($package; "tag-separator"; "-") as $separator
        | (if $include_v then "v" + $version else $version end) as $version_tag
        | {
            path: $path,
            component: $component,
            tag_name: (if $include_component then $component + $separator + $version_tag else $version_tag end),
            version: $version,
            sha: $release_sha
          }
      ];

    def valid_catalog:
      type == "array" and all(.[ ];
        (.id | type) == "number" and .id > 0
        and (.tag_name | type) == "string" and (.tag_name | length) > 0
        and (.draft | type) == "boolean");

    def with_release_identity($release):
      ([$after_releases[] | select(.tag_name == $release.tag_name)]) as $matches
      | if ($matches | length) != 1 then
          error("expected exactly one GitHub release for tag " + $release.tag_name)
        else
          $release + {release_id: $matches[0].id}
        end;

    if (($before_releases | valid_catalog) and ($after_releases | valid_catalog)) | not then
      error("release catalogs must contain id, tag_name, and draft")
    else . end
    | ($before_releases | map(.id)) as $before_ids
    | ([$after_releases[] | select(.id as $id | $before_ids | index($id) | not) | .tag_name] | unique) as $new_tags
    | ($after_releases | map(.tag_name) | unique) as $after_tags
    | (if $replay_tag == "" then []
       elif $after_tags | index($replay_tag) then [$replay_tag]
       else error("requested replay tag is not a GitHub release: " + $replay_tag)
       end) as $replay_tags
    | ($new_tags + $replay_tags | unique) as $recoverable_tags
    | (normalized($first_paths; $first_outputs)
       + normalized($retry_paths; $retry_outputs)
       | unique_by(.tag_name)) as $reported
    | (expected_releases | map(select(.tag_name as $tag | $recoverable_tags | index($tag)))) as $recovered
    # Release Please outputs are authoritative for releases created by this
    # invocation. The checked-out manifest can legitimately lag when another
    # release commit reaches the target branch while this run is queued.
    | ($recoverable_tags - (($reported + $recovered) | map(.tag_name) | unique)) as $unmatched
    | if $unmatched | length > 0 then
        error("new GitHub release tag(s) do not match the release manifest: " + ($unmatched | join(", ")))
      else
        ($reported + $recovered
          | unique_by(.tag_name)
          | map(with_release_identity(.)))
      end
  '
