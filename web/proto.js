/* ============ PROTOTYPE KNOBS — NOT PART OF THE BOARD ============
   No persistence, deliberately: this panel exists for one sitting, to settle
   four values. Whatever wins gets written into board.html by hand. */
(function(){
  var FACES = {
    system:'-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif',
    avenir:'"Avenir Next","Avenir",system-ui,sans-serif',
    verdana:'Verdana,"DejaVu Sans",Geneva,sans-serif',
    charter:'Charter,"Iowan Old Style",Georgia,"Times New Roman",serif'
  };
  var state = {theme:'dark', fs:'1', face:'system', lhx:'1'};
  var kb = document.getElementById('kb');
  var root = document.documentElement;

  function apply(){
    root.setAttribute('data-theme', state.theme);
    root.style.setProperty('--fs', state.fs);
    root.style.setProperty('--lhx', state.lhx);
    root.style.setProperty('--face', FACES[state.face]);
    kb.querySelectorAll('.kbseg').forEach(function(seg){
      var k = seg.dataset.knob;
      seg.querySelectorAll('button').forEach(function(b){
        b.classList.toggle('on', b.dataset.v === state[k]);
      });
    });
    document.getElementById('kbnow').textContent =
      state.theme + ' \u00b7 ' + state.fs + '\u00d7 \u00b7 ' + state.face +
      ' \u00b7 lh ' + state.lhx;
  }

  kb.addEventListener('click', function(e){
    var b = e.target.closest('button');
    if (!b) return;
    if (b.id === 'kbdot') { kb.classList.add('open'); return; }
    if (b.id === 'kbclose') { kb.classList.remove('open'); return; }
    var seg = b.closest('.kbseg');
    if (!seg) return;
    state[seg.dataset.knob] = b.dataset.v;
    apply();
  });

  apply();
})();
