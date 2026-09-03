/**
 * Canvas physics for the Draft Hub lottery cage — balls, spokes, collisions.
 */
(function (global) {
  "use strict";

  var SPOKE_COUNT = 4;
  var BALL_LABELS = 14;

  function distPointSeg(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1;
    var dy = y2 - y1;
    var len2 = dx * dx + dy * dy;
    if (len2 === 0) {
      var ex = px - x1;
      var ey = py - y1;
      return Math.sqrt(ex * ex + ey * ey);
    }
    var t = ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    var cx = x1 + t * dx;
    var cy = y1 + t * dy;
    var rx = px - cx;
    var ry = py - cy;
    return Math.sqrt(rx * rx + ry * ry);
  }

  function LotteryCageSim(canvas, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.opts = opts || {};
    this.baseMode = opts.locked ? "locked" : opts.live ? "live" : "idle";
    this.mode = opts.fast ? "fast" : this.baseMode;
    this.spokeAngle = Math.random() * Math.PI * 2;
    this.balls = [];
    this.raf = null;
    this.running = false;
    this.resizeObserver = null;
    this._onResize = this.resize.bind(this);
    this.resize();
    this.initBalls();
  }

  LotteryCageSim.prototype.speedForMode = function (mode) {
    if (mode === "fast") return 0.0525;
    if (mode === "live") return 0.0206;
    if (mode === "locked") return 0.00675;
    return 0.0105;
  };

  LotteryCageSim.prototype.setMode = function (mode) {
    this.mode = mode || this.baseMode;
  };

  LotteryCageSim.prototype.setFast = function (on) {
    this.mode = on ? "fast" : this.baseMode;
  };

  LotteryCageSim.prototype.setBaseMode = function (live, locked) {
    this.baseMode = locked ? "locked" : live ? "live" : "idle";
    if (this.mode !== "fast") this.mode = this.baseMode;
  };

  LotteryCageSim.prototype.resize = function () {
    var parent = this.canvas.parentElement;
    if (!parent) return;
    var rect = parent.getBoundingClientRect();
    var size = Math.max(rect.width, rect.height) || 240;
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(size * dpr);
    this.canvas.height = Math.round(size * dpr);
    this.canvas.style.width = size + "px";
    this.canvas.style.height = size + "px";
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = size;
    this.h = size;
    this.cx = size / 2;
    this.cy = size / 2;
    this.boundsR = size * 0.41;
    this.ballR = size * 0.058;
    this.spokeLen = this.boundsR * 0.73;
    this.spokeHalfW = this.ballR * 0.42;
    this.hubR = size * 0.07;
    this.gravity = size * 0.00028;
    this.maxBallSpeed = size * 0.18;
  };

  LotteryCageSim.prototype.initBalls = function () {
    this.balls = [];
    var i;
    for (i = 1; i <= BALL_LABELS; i += 1) {
      var ang = Math.random() * Math.PI * 2;
      var rad = Math.random() * this.boundsR * 0.72;
      this.balls.push({
        n: i,
        x: this.cx + Math.cos(ang) * rad,
        y: this.cy + Math.sin(ang) * rad,
        vx: (Math.random() - 0.5) * 2.4,
        vy: (Math.random() - 0.5) * 2.4,
        r: this.ballR,
      });
    }
  };

  LotteryCageSim.prototype.collideWall = function (ball) {
    var dx = ball.x - this.cx;
    var dy = ball.y - this.cy;
    var dist = Math.sqrt(dx * dx + dy * dy) || 0.001;
    var maxDist = this.boundsR - ball.r;
    if (dist <= maxDist) return;
    var nx = dx / dist;
    var ny = dy / dist;
    ball.x = this.cx + nx * maxDist;
    ball.y = this.cy + ny * maxDist;
    var vDot = ball.vx * nx + ball.vy * ny;
    if (vDot > 0) {
      ball.vx -= vDot * nx * 1.65;
      ball.vy -= vDot * ny * 1.65;
    }
  };

  LotteryCageSim.prototype.collideSpoke = function (ball, x1, y1, x2, y2, angVel) {
    var dist = distPointSeg(ball.x, ball.y, x1, y1, x2, y2);
    var minDist = ball.r + this.spokeHalfW;
    if (dist >= minDist) return;

    var dx = x2 - x1;
    var dy = y2 - y1;
    var len2 = dx * dx + dy * dy || 1;
    var t = ((ball.x - x1) * dx + (ball.y - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    var cx = x1 + t * dx;
    var cy = y1 + t * dy;
    var nx = ball.x - cx;
    var ny = ball.y - cy;
    var nLen = Math.sqrt(nx * nx + ny * ny) || 0.001;
    nx /= nLen;
    ny /= nLen;

    ball.x += nx * (minDist - dist);
    ball.y += ny * (minDist - dist);

    var vDot = ball.vx * nx + ball.vy * ny;
    if (vDot < 0) {
      ball.vx -= vDot * nx * 1.55;
      ball.vy -= vDot * ny * 1.55;
    }

    var armR = Math.sqrt((cx - this.cx) * (cx - this.cx) + (cy - this.cy) * (cy - this.cy));
    var tx = -(cy - this.cy) / (armR || 1);
    var ty = (cx - this.cx) / (armR || 1);
    var kick = angVel * armR * 0.32;
    ball.vx += tx * kick;
    ball.vy += ty * kick;
  };

  LotteryCageSim.prototype.collideBalls = function () {
    var i;
    var j;
    for (i = 0; i < this.balls.length; i += 1) {
      for (j = i + 1; j < this.balls.length; j += 1) {
        var a = this.balls[i];
        var b = this.balls[j];
        var dx = b.x - a.x;
        var dy = b.y - a.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 0.001;
        var minDist = a.r + b.r;
        if (dist >= minDist) continue;
        var nx = dx / dist;
        var ny = dy / dist;
        var overlap = minDist - dist;
        a.x -= nx * overlap * 0.5;
        a.y -= ny * overlap * 0.5;
        b.x += nx * overlap * 0.5;
        b.y += ny * overlap * 0.5;
        var dvx = a.vx - b.vx;
        var dvy = a.vy - b.vy;
        var dvn = dvx * nx + dvy * ny;
        if (dvn <= 0) {
          var impulse = -dvn * 0.82;
          a.vx += impulse * nx;
          a.vy += impulse * ny;
          b.vx -= impulse * nx;
          b.vy -= impulse * ny;
        }
      }
    }
  };

  LotteryCageSim.prototype.step = function () {
    var angVel = this.speedForMode(this.mode);
    this.spokeAngle += angVel;

    var i;
    var s;
    var friction = this.mode === "fast" ? 0.992 : 0.988;
    var gravity = this.gravity * (this.mode === "fast" ? 1.15 : 1);
    for (i = 0; i < this.balls.length; i += 1) {
      var ball = this.balls[i];
      ball.vy += gravity;
      ball.vx *= friction;
      ball.vy *= friction;
      if (this.mode === "fast") {
        ball.vx += (Math.random() - 0.5) * 0.35;
        ball.vy += (Math.random() - 0.5) * 0.35;
      }
      var spd = Math.sqrt(ball.vx * ball.vx + ball.vy * ball.vy);
      if (spd > this.maxBallSpeed) {
        var scale = this.maxBallSpeed / spd;
        ball.vx *= scale;
        ball.vy *= scale;
      }
      ball.x += ball.vx;
      ball.y += ball.vy;
    }

    for (s = 0; s < SPOKE_COUNT; s += 1) {
      var a = this.spokeAngle + (Math.PI * 2 * s) / SPOKE_COUNT;
      var x1 = this.cx;
      var y1 = this.cy;
      var x2 = this.cx + Math.cos(a) * this.spokeLen;
      var y2 = this.cy + Math.sin(a) * this.spokeLen;
      for (i = 0; i < this.balls.length; i += 1) {
        this.collideSpoke(this.balls[i], x1, y1, x2, y2, angVel);
      }
    }

    for (i = 0; i < this.balls.length; i += 1) {
      this.collideWall(this.balls[i]);
    }
    this.collideBalls();
  };

  LotteryCageSim.prototype.drawBall = function (ball) {
    var ctx = this.ctx;
    var grd = ctx.createRadialGradient(
      ball.x - ball.r * 0.35,
      ball.y - ball.r * 0.35,
      ball.r * 0.15,
      ball.x,
      ball.y,
      ball.r
    );
    grd.addColorStop(0, "#ffffff");
    grd.addColorStop(0.72, "#c8d4e0");
    grd.addColorStop(1, "#8fa3b5");
    ctx.beginPath();
    ctx.arc(ball.x, ball.y, ball.r, 0, Math.PI * 2);
    ctx.fillStyle = grd;
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.15)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = "#0a1620";
    ctx.font = "bold " + Math.max(10, Math.round(ball.r * 1.05)) + "px system-ui,sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(ball.n), ball.x, ball.y + 0.5);
  };

  LotteryCageSim.prototype.drawSpoke = function (angle) {
    var ctx = this.ctx;
    var x2 = this.cx + Math.cos(angle) * this.spokeLen;
    var y2 = this.cy + Math.sin(angle) * this.spokeLen;
    ctx.save();
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(255, 215, 96, 0.95)";
    ctx.lineWidth = this.spokeHalfW * 2;
    ctx.shadowColor = "rgba(255, 215, 96, 0.45)";
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.moveTo(this.cx, this.cy);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.restore();
  };

  LotteryCageSim.prototype.draw = function () {
    var ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);

    var floorGrd = ctx.createRadialGradient(this.cx, this.cy + this.boundsR * 0.35, 4, this.cx, this.cy, this.boundsR);
    floorGrd.addColorStop(0, "rgba(64, 224, 208, 0.12)");
    floorGrd.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = floorGrd;
    ctx.beginPath();
    ctx.arc(this.cx, this.cy, this.boundsR, 0, Math.PI * 2);
    ctx.fill();

    var i;
    for (i = 0; i < this.balls.length; i += 1) {
      this.drawBall(this.balls[i]);
    }

    ctx.beginPath();
    ctx.arc(this.cx, this.cy, this.hubR, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255, 215, 96, 0.85)";
    ctx.fill();

    for (i = 0; i < SPOKE_COUNT; i += 1) {
      this.drawSpoke(this.spokeAngle + (Math.PI * 2 * i) / SPOKE_COUNT);
    }
  };

  LotteryCageSim.prototype.loop = function () {
    if (!this.running) return;
    this.step();
    this.draw();
    this.raf = global.requestAnimationFrame(this.loop.bind(this));
  };

  LotteryCageSim.prototype.start = function () {
    if (this.running) return;
    this.running = true;
    global.addEventListener("resize", this._onResize);
    this.loop();
  };

  LotteryCageSim.prototype.destroy = function () {
    this.running = false;
    if (this.raf) {
      global.cancelAnimationFrame(this.raf);
      this.raf = null;
    }
    global.removeEventListener("resize", this._onResize);
  };

  global.LotteryCageSim = LotteryCageSim;
})(window);
