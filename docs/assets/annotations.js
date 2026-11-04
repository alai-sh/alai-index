// Code annotations open when their marker is clicked or focused. This opens one on hover too, by focusing its
// marker as a click would, and closes it once the pointer leaves the marker and its tooltip. The listeners are on the
// document, so they keep working as Zensical swaps pages in place.
document.addEventListener("mouseover", (event) => {
  const marker = event.target.closest?.(".md-annotation__index");
  if (marker && document.activeElement !== marker) {
    marker.focus({ preventScroll: true });
  }
});

document.addEventListener("mouseout", (event) => {
  const annotation = event.target.closest?.(".md-annotation");
  if (annotation && !annotation.contains(event.relatedTarget) && annotation.contains(document.activeElement)) {
    document.activeElement.blur();
  }
});
