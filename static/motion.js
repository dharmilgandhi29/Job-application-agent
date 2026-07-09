/* motion.js — shared motion behaviors. Safe to load on every page.
   - reveals [data-reveal] elements as they scroll into view
   - toggles .scrolled on any .js-nav when the page scrolls
   - drives [data-parallax] elements (landing page opt-in)
   All guarded so pages without these elements simply do nothing. */
(function(){
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function initReveals(){
    var els = document.querySelectorAll("[data-reveal]");
    if(!els.length) return;
    if(reduce || !("IntersectionObserver" in window)){
      els.forEach(function(el){ el.classList.add("in"); });
      return;
    }
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){ e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, {threshold:0.12});
    els.forEach(function(el){ io.observe(el); });
  }

  function initNav(){
    var navs = document.querySelectorAll(".js-nav");
    if(!navs.length) return;
    function onScroll(){
      var s = window.scrollY > 24;
      navs.forEach(function(n){ n.classList.toggle("scrolled", s); });
    }
    window.addEventListener("scroll", onScroll, {passive:true});
    onScroll();
  }

  function initParallax(){
    var els = document.querySelectorAll("[data-parallax]");
    if(!els.length || reduce) return;
    var ticking = false;
    window.addEventListener("scroll", function(){
      if(ticking) return;
      ticking = true;
      requestAnimationFrame(function(){
        var y = window.scrollY;
        els.forEach(function(el){
          var speed = parseFloat(el.getAttribute("data-parallax")) || 0;
          el.style.transform = "translateY(" + (y * speed) + "px)";
        });
        ticking = false;
      });
    }, {passive:true});
  }

  function boot(){ initReveals(); initNav(); initParallax(); }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
