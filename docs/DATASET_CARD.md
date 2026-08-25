# Dataset Card — Signoff-Lab-v0

Status: collection not started. Raw videos live in object storage, never in Git;
only manifests and annotations are committed under datasets/manifests/.

Target composition (SDD §42): 500 normal cycles, 100 idle episodes, 50+ each of
failed grasps, dropped objects, interrupted cycles — varied across camera position,
lighting, robot speed, object color/background/position.

Split by complete sessions (60/20/20) plus an out-of-distribution test set
(unseen lighting/object/camera). Never split by random frames.

Annotation schema and guidelines: see SDD §44.
