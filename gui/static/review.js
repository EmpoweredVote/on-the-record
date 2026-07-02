// Clip playback: clicking a clip button seeks the shared media element and plays.
document.addEventListener("click", function (e) {
  const btn = e.target.closest(".clip");
  if (!btn) return;
  const player = document.getElementById("player");
  if (!player) return;
  const seek = parseFloat(btn.getAttribute("data-seek"));
  if (!Number.isNaN(seek)) {
    player.currentTime = seek;
    player.play();
  }
});
