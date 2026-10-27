// Home Assistant serves its legacy build to browsers it counts as old
// (Safari and the iOS app before iOS 18.1, for one), and that build loads
// an integration's scripts as classic scripts, not modules. The card is a
// module, so this classic script imports it from beside itself.
(function () {
  var me = document.currentScript && document.currentScript.src;
  if (!me) return;
  import(me.replace("/legacy-loader.js", "/crestron-xpanel-card.js")).catch(function (err) {
    console.error("could not load crestron-xpanel-card.js", err);
  });
})();
