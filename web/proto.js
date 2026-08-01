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

  /* WHICH face actually rendered. A stack silently falls through, so on a
     phone you cannot tell Charter from Georgia from Times by looking — and
     the answer decides whether a serif is safe to ship or needs a webfont.
     Width-comparison rather than document.fonts.check, which is unreliable
     for locally-installed families: set the candidate ahead of a generic,
     and if the measured width moved, the candidate exists. */
  function have(name){
    var c = document.createElement('canvas').getContext('2d');
    var s = 'mmmmmmmmwwwwwwwwiiiiiiiil1I0O';
    return ['monospace','serif','sans-serif'].some(function(g){
      c.font = '72px ' + g;
      var base = c.measureText(s).width;
      c.font = '72px "' + name + '",' + g;
      return c.measureText(s).width !== base;
    });
  }
  var PROBE = ['Charter','Iowan Old Style','Georgia','Palatino','Baskerville',
               'Hoefler Text','Times New Roman','Avenir Next','Verdana'];

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
    document.getElementById('kbfonts').textContent =
      'on this device: ' + PROBE.filter(have).join(', ');
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
