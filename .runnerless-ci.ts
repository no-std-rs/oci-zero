import { badges, BadgeSpec } from "runnerless/v1";
import {
  ArtifactComment, ArtifactRetention, ArtifactSpec, ArtifactStatus,
  artifacts, checks, configuration, event, jobs, OperationRef, OperationRequest,
  packages, RegistryPublishRequest, repo, Value, workflow, WriteResult,
} from "runnerless/v1";

const comparison = new ArtifactSpec("public-artifacts",
  new ArtifactStatus("Complexity comparison", 130678843, "https://pawel.buildbuddy.io"), "comparison-files")
  .file("comment.md", "text/plain", 24576)
  .file("publication.json", "application/json", 4096)
  .file("report.json", "application/json", 16777216)
  .file("report.md", "text/plain", 16777216)
  .file("baseline.md", "text/plain", 1048576)
  .file("baseline.json", "application/json", 16777216);
comparison.retention = new ArtifactRetention(14, 5);
comparison.comment = ArtifactComment.generated("comment.md", "publication.json", 24576);

const program = workflow()
  .publishArtifacts("complexity", comparison)
  .releasePlease();

export function configure(): void {
  configuration.titlePolicy();
  badges.define(new BadgeSpec("badge", "medium", "slowest-job"));
  program.configure();
  configuration.workflow("artifact-cleanup", ["pull_request"]);
  configuration.workflow("crate-publishing", ["workflow_run", "runnerless_completion"]);
}

export function run(): void {
  program.run();
  if (event.isMerge()) artifacts.cleanup();
  publishCrates(event.details());
}

// Actions supplies verified upload assets; registry credentials and writes stay
// inside Runnerless. Each independently versioned crate retains its own tag.
function publicationResult(name: string): void {
  const result = jobs.lookup<WriteResult>(new OperationRef<WriteResult>("op:publish-" + name));
  if (!result.ok) return;
  const value = result.value;
  checks.report("crates.io / " + name, value.get("status").string() == "succeeded"
    && value.get("value").get("failed").json == "false",
    "crates.io publication: " + value.get("status").string() + " "
      + value.get("value").get("error").string());
}

function stageCrate(details: Value, name: string, versionFile: string,
  first: OperationRef<WriteResult> | null = null,
  second: OperationRef<WriteResult> | null = null): OperationRef<WriteResult> | null {
  const version = repo.readText(versionFile, "head").trim();
  const tag = name + "-v" + version;
  if (details.get("workflowPath").string() == ".github/workflows/publish-release.yml"
    && details.get("headRef").string() != tag) return null;
  // This also fences CI recovery to the immutable release's exact source SHA.
  if (!packages.releasePublished(tag, version)) return null;
  const operation = new OperationRequest("publish-" + name);
  if (first !== null) operation.after(first);
  if (second !== null) operation.after(second);
  return packages.crates(operation, new RegistryPublishRequest(tag, version)
    .add(name, name + "-" + version + ".cargo-upload"));
}

function publishCrates(details: Value): void {
  if (details.get("event").string() == "runnerless_completion") {
    publicationResult("gzip-zero");
    publicationResult("zstd-zero");
    publicationResult("oci-zero");
    return;
  }
  if (details.get("event").string() != "workflow_run"
    || details.get("action").string() != "completed"
    || details.get("conclusion").string() != "success"
    || !details.get("pullNumber").isNull()) return;
  const path = details.get("workflowPath").string();
  const preparedRelease = path == ".github/workflows/publish-release.yml";
  // An existing release predating this workflow can prepare assets from current
  // main, then replay its existing successful CI at the exact release revision.
  // Ordinary current-main CI is not a release-asset completion signal.
  const recovery = path == ".github/workflows/ci.yml"
    && details.get("headRef").string() == details.get("baseRef").string()
    && details.get("head").string() != details.get("base").string();
  if (!preparedRelease && !recovery) return;
  const gzip = stageCrate(details, "gzip-zero", "gzip-zero/version.txt");
  const zstd = stageCrate(details, "zstd-zero", "zstd-zero/version.txt");
  stageCrate(details, "oci-zero", "version.txt", gzip, zstd);
}
