/**
 * OpenTrustSeal Embed Script v0.3
 *
 * Usage:
 *   <script src="https://api.opentrustseal.com/embed.js" data-domain="yoursite.com"></script>
 *
 * What this script does:
 *   1. Fetches the latest signed trust token from the OTT API
 *   2. Injects <meta> tags so agents can discover trust data
 *   3. Injects a <link> tag pointing to the token endpoint
 *   No visual output. No cookies. No tracking.
 */
(function() {
  'use strict';

  var API = 'https://api.opentrustseal.com';
  var script = document.currentScript;
  if (!script) return;

  var domain = script.getAttribute('data-domain');
  if (!domain) return;

  var link = document.createElement('link');
  link.rel = 'ott-trust-token';
  link.href = API + '/v1/token/' + encodeURIComponent(domain) + '/ott.json';
  link.type = 'application/json';
  document.head.appendChild(link);

  fetch(API + '/v1/token/' + encodeURIComponent(domain) + '/ott.json')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var metas = [
        { name: 'ott:domain', content: data.domain },
        { name: 'ott:score', content: String(data.trustScore) },
        { name: 'ott:recommendation', content: data.recommendation },
        { name: 'ott:model', content: data.scoringModel },
        { name: 'ott:checked', content: data.checkedAt },
        { name: 'ott:expires', content: data.expiresAt },
        { name: 'ott:signature', content: data.signature },
        { name: 'ott:issuer', content: data.issuer }
      ];

      for (var i = 0; i < metas.length; i++) {
        var meta = document.createElement('meta');
        meta.name = metas[i].name;
        meta.content = metas[i].content;
        document.head.appendChild(meta);
      }
    })
    .catch(function() {});
})();
