#!/usr/bin/env bash
set -euo pipefail

: "${CONFIG_FILE:?CONFIG_FILE is required}"
: "${MANIFEST_FILE:?MANIFEST_FILE is required}"

first_paths="${FIRST_PATHS:-}"
first_outputs="${FIRST_OUTPUTS:-}"
retry_paths="${RETRY_PATHS:-}"
retry_outputs="${RETRY_OUTPUTS:-}"
before_tags="${BEFORE_TAGS:-}"
after_tags="${AFTER_TAGS:-}"
release_sha="${RELEASE_SHA:-}"
replay_tag="${REPLAY_TAG:-}"

[ -n "$first_paths" ] || first_paths='[]'
[ -n "$first_outputs" ] || first_outputs='{}'
[ -n "$retry_paths" ] || retry_paths='[]'
[ -n "$retry_outputs" ] || retry_outputs='{}'
[ -n "$before_tags" ] || before_tags='[]'
[ -n "$after_tags" ] || after_tags='[]'

jq -cn \
  --slurpfile config "$CONFIG_FILE" \
  --slurpfile manifest "$MANIFEST_FILE" \
  --argjson first_paths "$first_paths" \
  --argjson first_outputs "$first_outputs" \
  --argjson retry_paths "$retry_paths" \
  --argjson retry_outputs "$retry_outputs" \
  --argjson before_tags "$before_tags" \
  --argjson after_tags "$after_tags" \
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

    ($after_tags - $before_tags | unique) as $new_tags
    | (if $replay_tag == "" then []
       elif $after_tags | index($replay_tag) then [$replay_tag]
       else error("requested replay tag is not a GitHub release: " + $replay_tag)
       end) as $replay_tags
    | ($new_tags + $replay_tags | unique) as $recoverable_tags
    | (expected_releases | map(select(.tag_name as $tag | $recoverable_tags | index($tag)))) as $recovered
    | ($recoverable_tags - ($recovered | map(.tag_name))) as $unmatched
    | if $unmatched | length > 0 then
        error("new GitHub release tag(s) do not match the release manifest: " + ($unmatched | join(", ")))
      else
        (normalized($first_paths; $first_outputs)
          + normalized($retry_paths; $retry_outputs)
          + $recovered
          | unique_by(.tag_name))
      end
  '
