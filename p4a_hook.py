import re
import shutil
from pathlib import Path


TV_KEY_FIELDS = """
    private static final java.util.concurrent.ConcurrentLinkedQueue<Integer> tvKeyQueue =
            new java.util.concurrent.ConcurrentLinkedQueue<Integer>();

    public static int pollTvKey() {
        Integer v = tvKeyQueue.poll();
        return v == null ? -1 : v.intValue();
    }

    private static boolean isTvNavKey(int keyCode) {
        switch (keyCode) {
            case android.view.KeyEvent.KEYCODE_DPAD_UP:
            case android.view.KeyEvent.KEYCODE_DPAD_DOWN:
            case android.view.KeyEvent.KEYCODE_DPAD_LEFT:
            case android.view.KeyEvent.KEYCODE_DPAD_RIGHT:
            case android.view.KeyEvent.KEYCODE_DPAD_CENTER:
            case android.view.KeyEvent.KEYCODE_ENTER:
            case android.view.KeyEvent.KEYCODE_NUMPAD_ENTER:
            case android.view.KeyEvent.KEYCODE_BACK:
            case android.view.KeyEvent.KEYCODE_MENU:
            case android.view.KeyEvent.KEYCODE_CHANNEL_UP:
            case android.view.KeyEvent.KEYCODE_CHANNEL_DOWN:
            case android.view.KeyEvent.KEYCODE_GUIDE:
            case android.view.KeyEvent.KEYCODE_INFO:
            case android.view.KeyEvent.KEYCODE_CAPTIONS:
            case android.view.KeyEvent.KEYCODE_MEDIA_AUDIO_TRACK:
            case android.view.KeyEvent.KEYCODE_PROG_RED:
            case android.view.KeyEvent.KEYCODE_PROG_GREEN:
            case android.view.KeyEvent.KEYCODE_PROG_YELLOW:
            case android.view.KeyEvent.KEYCODE_PROG_BLUE:
            case android.view.KeyEvent.KEYCODE_DEL:
            case android.view.KeyEvent.KEYCODE_0:
            case android.view.KeyEvent.KEYCODE_1:
            case android.view.KeyEvent.KEYCODE_2:
            case android.view.KeyEvent.KEYCODE_3:
            case android.view.KeyEvent.KEYCODE_4:
            case android.view.KeyEvent.KEYCODE_5:
            case android.view.KeyEvent.KEYCODE_6:
            case android.view.KeyEvent.KEYCODE_7:
            case android.view.KeyEvent.KEYCODE_8:
            case android.view.KeyEvent.KEYCODE_9:
                return true;
            default:
                return false;
        }
    }
"""

TV_DISPATCH_BODY = """
        int _tvKc = event.getKeyCode();
        if (isTvNavKey(_tvKc)) {
            if (event.getAction() == android.view.KeyEvent.ACTION_DOWN) {
                boolean _allowRep = (_tvKc == android.view.KeyEvent.KEYCODE_DPAD_UP
                        || _tvKc == android.view.KeyEvent.KEYCODE_DPAD_DOWN
                        || _tvKc == android.view.KeyEvent.KEYCODE_DPAD_LEFT
                        || _tvKc == android.view.KeyEvent.KEYCODE_DPAD_RIGHT
                        || _tvKc == android.view.KeyEvent.KEYCODE_DEL);
                if (event.getRepeatCount() == 0 || _allowRep) {
                    tvKeyQueue.offer(_tvKc);
                }
            }
            return true;
        }
"""

TV_VLC_CONTROLLER_JAVA = r"""package org.kivy.android;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.util.DisplayMetrics;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.TextureView;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.videolan.libvlc.LibVLC;
import org.videolan.libvlc.Media;
import org.videolan.libvlc.MediaPlayer;
import org.videolan.libvlc.interfaces.IMedia;
import org.videolan.libvlc.interfaces.IVLCVout;

import java.io.File;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public class TvVlcController {
    private static final String TAG = "TvVlcController";

    private static final String[] PL_DAYS_SHORT = {"", "ndz", "pon", "wt", "śr", "czw", "pt", "sob"};
    private static final String[] PL_MONTHS_SHORT = {"sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"};

    private static class TrackItem {
        final int id;
        final String label;
        final boolean active;
        final boolean selectable;

        TrackItem(int id, String label, boolean active, boolean selectable) {
            this.id = id;
            this.label = label;
            this.active = active;
            this.selectable = selectable;
        }
    }

    private static LibVLC libVlc;
    private static MediaPlayer mediaPlayer;
    private static TextureView videoView;

    // Infobar Enigma2 (natywny Android overlay nad TextureView)
    private static LinearLayout infobar;
    private static TextView ibChannel;
    private static TextView ibLiveBadge;
    private static TextView ibClock;
    private static TextView ibNowTime;
    private static TextView ibNowTitle;
    private static TextView ibNowRem;
    private static View ibProgFill;
    private static View ibProgEmpty;
    private static TextView ibNextTime;
    private static TextView ibNextTitle;
    private static TextView ibNextDur;
    private static ImageView ibPicon;
    private static FrameLayout ibPiconWrap;
    private static TextView ibStreamInfo;

    // Nakładka wyboru ścieżki Audio i Napisów
    private static LinearLayout trackPanel;
    private static TextView tabAudioBtn;
    private static TextView tabSubBtn;
    private static ScrollView trackScroll;
    private static LinearLayout trackListBox;
    private static final List<TrackItem> audioItems = new ArrayList<TrackItem>();
    private static final List<TrackItem> subItems = new ArrayList<TrackItem>();
    private static final List<TextView> trackRowViews = new ArrayList<TextView>();
    private static volatile boolean trackMenuOpen = false;
    private static int trackTab = 0;      // 0 = Audio, 1 = Napisy
    private static int trackSelIdx = 0;

    // Lekki górny toast (potwierdzenie zmiany audio/napisów/głośności)
    private static TextView topPill;

    // Dane bieżącego infobaru i rozdzielczość z OnNewVideoLayoutListener / fresh tracks
    private static long ibNowBeginSec = 0L;
    private static long ibNowDurSec = 0L;
    private static String ibSourceTag = "ENIGMA2";
    private static String ibLoadedPiconPath = "";

    private static volatile int lastVideoW = 0;
    private static volatile int lastVideoH = 0;
    private static volatile int lastSarNum = 0;
    private static volatile int lastSarDen = 0;

    private static volatile String currentUrl = "";
    private static volatile String lastStatus = "";
    private static volatile boolean active = false;
    private static volatile boolean inFullscreen = false;
    private static volatile int volume = 100;

    private static final Handler uiHandler = new Handler(Looper.getMainLooper());
    private static final Runnable hideInfobarRunnable = new Runnable() {
        @Override
        public void run() {
            if (infobar != null) {
                infobar.setVisibility(View.GONE);
            }
            uiHandler.removeCallbacks(infobarTickRunnable);
        }
    };
    private static final Runnable hideTopPillRunnable = new Runnable() {
        @Override
        public void run() {
            if (topPill != null) {
                topPill.setVisibility(View.GONE);
            }
        }
    };
    private static final Runnable infobarTickRunnable = new Runnable() {
        @Override
        public void run() {
            if (infobar != null && infobar.getVisibility() == View.VISIBLE) {
                refreshDynamicInfobarUi();
                uiHandler.postDelayed(this, 500L);
            }
        }
    };

    private static final IVLCVout.OnNewVideoLayoutListener videoLayoutListener =
            new IVLCVout.OnNewVideoLayoutListener() {
                @Override
                public void onNewVideoLayout(IVLCVout vlcVout, int width, int height,
                                             int visibleWidth, int visibleHeight, int sarNum, int sarDen) {
                    int w = visibleWidth > 0 ? visibleWidth : width;
                    int h = normalizeHeight(visibleHeight > 0 ? visibleHeight : height);
                    if (w > 0 && h > 0) {
                        lastVideoW = w;
                        lastVideoH = h;
                        if (sarNum > 0 && sarDen > 0) {
                            lastSarNum = sarNum;
                            lastSarDen = sarDen;
                        }
                        uiHandler.post(new Runnable() {
                            @Override
                            public void run() {
                                if (infobar != null && infobar.getVisibility() == View.VISIBLE) {
                                    refreshDynamicInfobarUi();
                                }
                            }
                        });
                    }
                }
            };

    private static final MediaPlayer.EventListener playerEventListener = new MediaPlayer.EventListener() {
        @Override
        public void onEvent(MediaPlayer.Event event) {
            if (event == null) {
                return;
            }
            switch (event.type) {
                case MediaPlayer.Event.Opening:
                    lastStatus = "VLC: Łączenie ze strumieniem...";
                    break;
                case MediaPlayer.Event.Buffering:
                    int pct = (int) event.getBuffering();
                    lastStatus = pct < 100 ? ("VLC: Buforowanie " + pct + "%") : "VLC: LIVE";
                    if (pct >= 100 && infobar != null && infobar.getVisibility() == View.VISIBLE) {
                        uiHandler.post(new Runnable() {
                            @Override
                            public void run() {
                                refreshDynamicInfobarUi();
                            }
                        });
                    }
                    break;
                case MediaPlayer.Event.Playing:
                case MediaPlayer.Event.Vout:
                case MediaPlayer.Event.ESAdded:
                case MediaPlayer.Event.ESSelected:
                    lastStatus = "VLC: LIVE";
                    uiHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            if (infobar != null && infobar.getVisibility() == View.VISIBLE) {
                                refreshDynamicInfobarUi();
                            }
                        }
                    });
                    break;
                case MediaPlayer.Event.EncounteredError:
                    lastStatus = "VLC BŁĄD: nie można otworzyć strumienia";
                    Log.e(TAG, "VLC EncounteredError for url=" + currentUrl);
                    break;
                case MediaPlayer.Event.EndReached:
                    lastStatus = "VLC: Strumień zakończony";
                    break;
                default:
                    break;
            }
        }
    };

    public static String getStatus() {
        return lastStatus == null ? "" : lastStatus;
    }

    public static boolean isActive() {
        return active;
    }

    public static boolean isTrackMenuOpen() {
        return trackMenuOpen;
    }

    private static Activity getActivity() {
        return PythonActivity.mActivity;
    }

    private static int dp(Activity a, float v) {
        return Math.round(TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, v, a.getResources().getDisplayMetrics()));
    }

    private static GradientDrawable makeRoundedBg(int fillColor, int strokeColor, int strokePx, float radiusPx) {
        GradientDrawable gd = new GradientDrawable();
        gd.setColor(fillColor);
        gd.setCornerRadius(radiusPx);
        if (strokePx > 0) {
            gd.setStroke(strokePx, strokeColor);
        }
        return gd;
    }

    private static TextView makeTv(Activity a, float spSize, int color, boolean bold) {
        TextView tv = new TextView(a);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, spSize);
        tv.setTextColor(color);
        if (bold) {
            tv.setTypeface(Typeface.DEFAULT_BOLD);
        }
        tv.setSingleLine(true);
        tv.setEllipsize(TextUtils.TruncateAt.END);
        return tv;
    }

    private static void buildInfobarUi(Activity a, FrameLayout content) {
        infobar = new LinearLayout(a);
        infobar.setOrientation(LinearLayout.VERTICAL);
        infobar.setBackground(makeRoundedBg(
                Color.argb(232, 8, 13, 22),
                Color.argb(175, 86, 196, 245),
                Math.max(2, dp(a, 1.2f)),
                dp(a, 12)
        ));
        infobar.setPadding(dp(a, 16), dp(a, 11), dp(a, 16), dp(a, 9));
        infobar.setVisibility(View.GONE);

        // === WIERSZ 1: NAZWA KANAŁU + STATUS + BIEŻĄCY CZAS ===
        LinearLayout topRow = new LinearLayout(a);
        topRow.setOrientation(LinearLayout.HORIZONTAL);
        topRow.setGravity(Gravity.CENTER_VERTICAL);

        ibChannel = makeTv(a, 20f, Color.rgb(102, 206, 250), true);
        LinearLayout.LayoutParams chLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        topRow.addView(ibChannel, chLp);

        ibLiveBadge = makeTv(a, 11.5f, Color.rgb(12, 24, 18), true);
        ibLiveBadge.setBackground(makeRoundedBg(Color.rgb(76, 224, 138), 0, 0, dp(a, 5)));
        ibLiveBadge.setPadding(dp(a, 8), dp(a, 2), dp(a, 8), dp(a, 2));
        ibLiveBadge.setText("LIVE");
        LinearLayout.LayoutParams badgeLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        badgeLp.setMargins(dp(a, 10), 0, dp(a, 12), 0);
        topRow.addView(ibLiveBadge, badgeLp);

        ibClock = makeTv(a, 16.5f, Color.rgb(225, 240, 255), true);
        ibClock.setGravity(Gravity.END);
        topRow.addView(ibClock, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        infobar.addView(topRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        View sep1 = new View(a);
        sep1.setBackgroundColor(Color.argb(90, 102, 200, 245));
        LinearLayout.LayoutParams sep1Lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, dp(a, 1)));
        sep1Lp.setMargins(0, dp(a, 6), 0, dp(a, 7));
        infobar.addView(sep1, sep1Lp);

        // === ŚRODEK: LEWO = PIKONA, PRAWO = 2 WIERSZE EPG + PASEK POSTĘPU ===
        LinearLayout midRow = new LinearLayout(a);
        midRow.setOrientation(LinearLayout.HORIZONTAL);
        midRow.setGravity(Gravity.CENTER_VERTICAL);

        ibPiconWrap = new FrameLayout(a);
        ibPiconWrap.setBackground(makeRoundedBg(
                Color.argb(190, 14, 20, 32),
                Color.argb(120, 90, 115, 140),
                1,
                dp(a, 8)
        ));
        ibPiconWrap.setPadding(dp(a, 6), dp(a, 5), dp(a, 6), dp(a, 5));

        ibPicon = new ImageView(a);
        ibPicon.setScaleType(ImageView.ScaleType.FIT_CENTER);
        ibPiconWrap.addView(ibPicon, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        LinearLayout.LayoutParams picLp = new LinearLayout.LayoutParams(dp(a, 126), dp(a, 74));
        picLp.setMargins(0, 0, dp(a, 14), 0);
        midRow.addView(ibPiconWrap, picLp);

        LinearLayout epgCol = new LinearLayout(a);
        epgCol.setOrientation(LinearLayout.VERTICAL);

        LinearLayout nowRow = new LinearLayout(a);
        nowRow.setOrientation(LinearLayout.HORIZONTAL);
        nowRow.setGravity(Gravity.CENTER_VERTICAL);

        ibNowTime = makeTv(a, 14.5f, Color.rgb(110, 208, 252), true);
        nowRow.addView(ibNowTime, new LinearLayout.LayoutParams(dp(a, 108), ViewGroup.LayoutParams.WRAP_CONTENT));

        ibNowTitle = makeTv(a, 16f, Color.rgb(245, 248, 252), true);
        LinearLayout.LayoutParams ntiLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        ntiLp.setMargins(dp(a, 6), 0, dp(a, 8), 0);
        nowRow.addView(ibNowTitle, ntiLp);

        ibNowRem = makeTv(a, 13.5f, Color.rgb(110, 208, 252), true);
        ibNowRem.setGravity(Gravity.END);
        nowRow.addView(ibNowRem, new LinearLayout.LayoutParams(dp(a, 78), ViewGroup.LayoutParams.WRAP_CONTENT));
        epgCol.addView(nowRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout progBar = new LinearLayout(a);
        progBar.setOrientation(LinearLayout.HORIZONTAL);
        progBar.setWeightSum(1000f);
        progBar.setBackground(makeRoundedBg(Color.argb(180, 28, 36, 48), Color.argb(100, 100, 120, 140), 1, dp(a, 3)));
        progBar.setPadding(1, 1, 1, 1);

        ibProgFill = new View(a);
        GradientDrawable progGrad = new GradientDrawable(
                GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{Color.rgb(42, 148, 255), Color.rgb(48, 224, 210), Color.rgb(104, 232, 118)}
        );
        progGrad.setCornerRadius(dp(a, 2));
        ibProgFill.setBackground(progGrad);
        ibProgEmpty = new View(a);
        ibProgEmpty.setBackgroundColor(Color.TRANSPARENT);

        progBar.addView(ibProgFill, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0f));
        progBar.addView(ibProgEmpty, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1000f));
        LinearLayout.LayoutParams pbLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(a, 6));
        pbLp.setMargins(0, dp(a, 5), 0, dp(a, 6));
        epgCol.addView(progBar, pbLp);

        LinearLayout nextRow = new LinearLayout(a);
        nextRow.setOrientation(LinearLayout.HORIZONTAL);
        nextRow.setGravity(Gravity.CENTER_VERTICAL);

        ibNextTime = makeTv(a, 13.5f, Color.rgb(160, 174, 190), false);
        nextRow.addView(ibNextTime, new LinearLayout.LayoutParams(dp(a, 108), ViewGroup.LayoutParams.WRAP_CONTENT));

        ibNextTitle = makeTv(a, 14.5f, Color.rgb(88, 212, 160), false);
        LinearLayout.LayoutParams nxtiLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        nxtiLp.setMargins(dp(a, 6), 0, dp(a, 8), 0);
        nextRow.addView(ibNextTitle, nxtiLp);

        ibNextDur = makeTv(a, 13f, Color.rgb(150, 164, 180), false);
        ibNextDur.setGravity(Gravity.END);
        nextRow.addView(ibNextDur, new LinearLayout.LayoutParams(dp(a, 78), ViewGroup.LayoutParams.WRAP_CONTENT));
        epgCol.addView(nextRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        midRow.addView(epgCol, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        infobar.addView(midRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        View sep2 = new View(a);
        sep2.setBackgroundColor(Color.argb(70, 120, 145, 170));
        LinearLayout.LayoutParams sep2Lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, dp(a, 1)));
        sep2Lp.setMargins(0, dp(a, 7), 0, dp(a, 5));
        infobar.addView(sep2, sep2Lp);

        // === DÓŁ: MAKSIMUM INFORMACJI O STRUMIENIU + PODPOWIEDŹ PILOTA ===
        ibStreamInfo = makeTv(a, 12.5f, Color.rgb(198, 214, 232), true);
        ibStreamInfo.setSingleLine(false);
        ibStreamInfo.setMaxLines(2);
        ibStreamInfo.setText("Analiza strumienia...");
        infobar.addView(ibStreamInfo, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView ibHint = makeTv(a, 10.5f, Color.rgb(132, 150, 170), false);
        ibHint.setText("[◄ / MENU] Audio & Napisy   •   [►] EPG   •   [▲ ▼] Kanał   •   [OK] Pasek   •   [WSTECZ] Lista");
        LinearLayout.LayoutParams hintLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hintLp.setMargins(0, dp(a, 3), 0, 0);
        infobar.addView(ibHint, hintLp);

        FrameLayout.LayoutParams ibLp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        ibLp.gravity = Gravity.BOTTOM;
        ibLp.setMargins(dp(a, 28), 0, dp(a, 28), dp(a, 18));
        content.addView(infobar, ibLp);
    }

    private static void buildTrackMenuUi(Activity a, FrameLayout content) {
        trackPanel = new LinearLayout(a);
        trackPanel.setOrientation(LinearLayout.VERTICAL);
        trackPanel.setBackground(makeRoundedBg(
                Color.argb(238, 9, 14, 24),
                Color.argb(215, 86, 196, 245),
                Math.max(2, dp(a, 1.5f)),
                dp(a, 14)
        ));
        trackPanel.setPadding(dp(a, 18), dp(a, 14), dp(a, 18), dp(a, 12));
        trackPanel.setVisibility(View.GONE);

        TextView title = makeTv(a, 17.5f, Color.rgb(102, 206, 250), true);
        title.setText("AUDIO I NAPISY");
        trackPanel.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout tabsRow = new LinearLayout(a);
        tabsRow.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams tabsLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tabsLp.setMargins(0, dp(a, 10), 0, dp(a, 10));

        tabAudioBtn = makeTv(a, 14.5f, Color.WHITE, true);
        tabAudioBtn.setGravity(Gravity.CENTER);
        tabAudioBtn.setPadding(dp(a, 10), dp(a, 8), dp(a, 10), dp(a, 8));
        LinearLayout.LayoutParams taLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        taLp.setMargins(0, 0, dp(a, 6), 0);
        tabsRow.addView(tabAudioBtn, taLp);

        tabSubBtn = makeTv(a, 14.5f, Color.WHITE, true);
        tabSubBtn.setGravity(Gravity.CENTER);
        tabSubBtn.setPadding(dp(a, 10), dp(a, 8), dp(a, 10), dp(a, 8));
        LinearLayout.LayoutParams tsLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        tsLp.setMargins(dp(a, 6), 0, 0, 0);
        tabsRow.addView(tabSubBtn, tsLp);

        trackPanel.addView(tabsRow, tabsLp);

        trackScroll = new ScrollView(a);
        trackListBox = new LinearLayout(a);
        trackListBox.setOrientation(LinearLayout.VERTICAL);
        trackScroll.addView(trackListBox, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout.LayoutParams scLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(a, 245));
        trackPanel.addView(trackScroll, scLp);

        TextView footer = makeTv(a, 11.5f, Color.rgb(148, 164, 184), false);
        footer.setGravity(Gravity.CENTER);
        footer.setText("▲▼ Wybór   •   ◄► Zakładka   •   OK Zatwierdź   •   WSTECZ Zamknij");
        LinearLayout.LayoutParams ftLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ftLp.setMargins(0, dp(a, 10), 0, 0);
        trackPanel.addView(footer, ftLp);

        FrameLayout.LayoutParams tpLp = new FrameLayout.LayoutParams(dp(a, 470), ViewGroup.LayoutParams.WRAP_CONTENT);
        tpLp.gravity = Gravity.CENTER;
        content.addView(trackPanel, tpLp);

        topPill = makeTv(a, 15.5f, Color.WHITE, true);
        topPill.setBackground(makeRoundedBg(
                Color.argb(232, 12, 24, 42),
                Color.argb(215, 76, 224, 138),
                Math.max(2, dp(a, 1.2f)),
                dp(a, 10)
        ));
        topPill.setPadding(dp(a, 20), dp(a, 10), dp(a, 20), dp(a, 10));
        topPill.setVisibility(View.GONE);
        FrameLayout.LayoutParams pillLp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        pillLp.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        pillLp.topMargin = dp(a, 24);
        content.addView(topPill, pillLp);
    }

    private static void recreateMediaPlayerUi() {
        if (mediaPlayer != null) {
            try {
                mediaPlayer.setEventListener(null);
                mediaPlayer.stop();
                IVLCVout vout = mediaPlayer.getVLCVout();
                if (vout.areViewsAttached()) {
                    vout.detachViews();
                }
                mediaPlayer.release();
            } catch (Throwable ignored) {
            }
            mediaPlayer = null;
        }
        if (libVlc != null) {
            mediaPlayer = new MediaPlayer(libVlc);
            mediaPlayer.setEventListener(playerEventListener);
        }
    }

    private static void ensureInitUi(final Activity activity) {
        if (libVlc != null && videoView != null) {
            if (mediaPlayer == null) {
                recreateMediaPlayerUi();
            }
            return;
        }
        ArrayList<String> opts = new ArrayList<String>();
        opts.add("--network-caching=800");
        opts.add("--live-caching=800");
        opts.add("--clock-jitter=0");
        opts.add("--http-reconnect");

        libVlc = new LibVLC(activity, opts);
        recreateMediaPlayerUi();

        final FrameLayout content = (FrameLayout) activity.findViewById(android.R.id.content);
        videoView = new TextureView(activity);
        videoView.setVisibility(View.GONE);
        videoView.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            @Override
            public void onLayoutChange(View v, int l, int t, int r, int b,
                                       int ol, int ot, int or, int ob) {
                int w = r - l;
                int h = b - t;
                if (w > 1 && h > 1 && mediaPlayer != null) {
                    try {
                        IVLCVout vout = mediaPlayer.getVLCVout();
                        if (vout.areViewsAttached()) {
                            vout.setWindowSize(w, h);
                        }
                    } catch (Throwable ignored) {
                    }
                }
            }
        });
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(2, 2);
        lp.gravity = Gravity.TOP | Gravity.LEFT;
        content.addView(videoView, lp);

        buildInfobarUi(activity, content);
        buildTrackMenuUi(activity, content);
    }

    /**
     * Czyści wewnętrzny cache mNativeTracks w org.videolan.libvlc.Media (LibVLC 3.x cache'uje
     * pierwszy wynik z momentów gdy MPEG-TS PMT nie ma jeszcze wymiarów width/height)
     * i wymusza świeży odczyt z natywnego libvlc_media_tracks_get().
     */
    private static IMedia.Track[] getFreshTracks(IMedia m) {
        if (m == null) return null;
        try {
            Class<?> cls = m.getClass();
            while (cls != null && cls != Object.class) {
                for (Field f : cls.getDeclaredFields()) {
                    if (f.getType().isArray()) {
                        Class<?> comp = f.getType().getComponentType();
                        if (comp != null && IMedia.Track.class.isAssignableFrom(comp)) {
                            f.setAccessible(true);
                            f.set(m, null);
                        }
                    }
                }
                cls = cls.getSuperclass();
            }
        } catch (Throwable ignored) {
        }
        try {
            int tc = m.getTrackCount();
            if (tc > 0) {
                IMedia.Track[] out = new IMedia.Track[tc];
                for (int i = 0; i < tc; i++) {
                    out[i] = m.getTrack(i);
                }
                return out;
            }
        } catch (Throwable ignored) {
        }
        try {
            Class<?> cls = m.getClass();
            while (cls != null && cls != Object.class) {
                try {
                    Method nm = cls.getDeclaredMethod("nativeGetTracks");
                    nm.setAccessible(true);
                    Object res = nm.invoke(m);
                    if (res instanceof IMedia.Track[]) {
                        return (IMedia.Track[]) res;
                    }
                    break;
                } catch (NoSuchMethodException ignored) {
                    cls = cls.getSuperclass();
                }
            }
        } catch (Throwable ignored) {
        }
        return null;
    }

    private static String mapCodecName(String raw) {
        if (raw == null || raw.trim().isEmpty()) {
            return "";
        }
        String c = raw.trim().toLowerCase(Locale.ROOT);
        if (c.equals("h264") || c.equals("avc1")) return "H.264";
        if (c.equals("hevc") || c.equals("h265") || c.equals("hvc1") || c.equals("hev1")) return "H.265/HEVC";
        if (c.equals("mpgv") || c.equals("mp2v") || c.equals("mpeg2")) return "MPEG-2";
        if (c.equals("mp4v")) return "MPEG-4";
        if (c.equals("av01")) return "AV1";
        if (c.equals("vp90") || c.equals("vp9")) return "VP9";
        if (c.equals("a52 ") || c.equals("a52") || c.equals("ac-3")) return "AC3";
        if (c.equals("eac3") || c.equals("ec-3")) return "E-AC3";
        if (c.equals("mpga") || c.equals("mp2a") || c.equals("mp3")) return "MPEG";
        if (c.equals("mp4a") || c.equals("aac")) return "AAC";
        if (c.equals("dts ") || c.equals("dts")) return "DTS";
        if (c.equals("opus")) return "OPUS";
        if (c.equals("dvbs")) return "DVB Sub";
        if (c.equals("telx")) return "Teletext";
        return raw.trim().toUpperCase(Locale.ROOT);
    }

    private static int normalizeHeight(int h) {
        if (h == 1088) return 1080;
        if (h == 544) return 540;
        return h;
    }

    private static String qualityTag(int w, int h) {
        if (h >= 2000 || w >= 3600) return "4K UHD";
        if (h >= 1000 || w >= 1800) return "FHD";
        if (h >= 700 || w >= 1200) return "HD";
        if (h > 0) return "SD";
        return "";
    }

    private static String channelsTag(int ch) {
        if (ch == 1) return "1.0";
        if (ch == 2) return "2.0";
        if (ch == 6) return "5.1";
        if (ch == 8) return "7.1";
        return ch > 0 ? (ch + "ch") : "";
    }

    private static String aspectTag(int w, int h, int sarNum, int sarDen) {
        if (w <= 0 || h <= 0) return "";
        double dar = ((double) w / (double) h);
        if (sarNum > 0 && sarDen > 0) {
            dar = dar * ((double) sarNum / (double) sarDen);
        }
        if (Math.abs(dar - (16.0 / 9.0)) < 0.14) return "16:9";
        if (Math.abs(dar - (4.0 / 3.0)) < 0.14) return "4:3";
        if (dar > 2.15) return "21:9";
        return String.format(Locale.US, "%.2f:1", dar);
    }

    private static float readFloatField(Object obj, String name) {
        try {
            Field f = obj.getClass().getField(name);
            return f.getFloat(obj);
        } catch (Throwable ignored) {
            return 0f;
        }
    }

    private static int readIntField(Object obj, String name) {
        try {
            Field f = obj.getClass().getField(name);
            return f.getInt(obj);
        } catch (Throwable ignored) {
            return -1;
        }
    }

    // === ODCZYT I RENDEROWANIE MENU ŚCIEŻEK AUDIO / NAPISÓW ===
    private static void populateTrackListsUi() {
        audioItems.clear();
        subItems.clear();
        if (mediaPlayer == null) {
            return;
        }

        Map<Integer, IMedia.AudioTrack> audioMeta = new HashMap<Integer, IMedia.AudioTrack>();
        Map<Integer, IMedia.Track> subMeta = new HashMap<Integer, IMedia.Track>();
        IMedia m = null;
        try {
            m = mediaPlayer.getMedia();
            IMedia.Track[] freshTracks = getFreshTracks(m);
            if (freshTracks != null) {
                for (IMedia.Track tr : freshTracks) {
                    if (tr == null) continue;
                    if (tr instanceof IMedia.AudioTrack) {
                        audioMeta.put(tr.id, (IMedia.AudioTrack) tr);
                    } else if (tr.type == IMedia.Track.Type.Text) {
                        subMeta.put(tr.id, tr);
                    }
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (m != null) {
                try {
                    m.release();
                } catch (Throwable ignored) {
                }
            }
        }

        int curAudio = -1;
        try {
            curAudio = mediaPlayer.getAudioTrack();
        } catch (Throwable ignored) {
        }
        MediaPlayer.TrackDescription[] aTracks = null;
        try {
            aTracks = mediaPlayer.getAudioTracks();
        } catch (Throwable ignored) {
        }

        if (aTracks != null) {
            for (MediaPlayer.TrackDescription td : aTracks) {
                if (td == null || td.id == -1) continue;
                String base = (td.name != null && !td.name.trim().isEmpty()) ? td.name.trim() : ("Audio #" + td.id);
                IMedia.AudioTrack meta = audioMeta.get(td.id);
                if (meta != null) {
                    StringBuilder extra = new StringBuilder();
                    String ac = mapCodecName(meta.codec != null && !meta.codec.isEmpty() ? meta.codec : meta.originalCodec);
                    if (!ac.isEmpty()) extra.append(ac);
                    String ch = channelsTag(meta.channels);
                    if (!ch.isEmpty()) {
                        if (extra.length() > 0) extra.append(" ");
                        extra.append(ch);
                    }
                    if (meta.bitrate > 0) {
                        if (extra.length() > 0) extra.append(" ");
                        extra.append(meta.bitrate / 1000).append("k");
                    }
                    if (meta.language != null && !meta.language.trim().isEmpty()
                            && !base.toLowerCase(Locale.ROOT).contains(meta.language.trim().toLowerCase(Locale.ROOT))) {
                        if (extra.length() > 0) extra.append(" ");
                        extra.append(meta.language.trim().toUpperCase(Locale.ROOT));
                    }
                    if (extra.length() > 0) {
                        base = base + "  (" + extra.toString() + ")";
                    }
                }
                audioItems.add(new TrackItem(td.id, base, td.id == curAudio, true));
            }
        }
        if (audioItems.isEmpty()) {
            audioItems.add(new TrackItem(-1, "Brak dodatkowych ścieżek audio", false, false));
        } else {
            audioItems.add(new TrackItem(-1, "Wyłącz dźwięk (brak ścieżki)", curAudio == -1, true));
        }

        int curSub = -1;
        try {
            curSub = mediaPlayer.getSpuTrack();
        } catch (Throwable ignored) {
        }
        MediaPlayer.TrackDescription[] sTracks = null;
        try {
            sTracks = mediaPlayer.getSpuTracks();
        } catch (Throwable ignored) {
        }

        List<TrackItem> foundSubs = new ArrayList<TrackItem>();
        if (sTracks != null) {
            for (MediaPlayer.TrackDescription td : sTracks) {
                if (td == null || td.id == -1) continue;
                String base = (td.name != null && !td.name.trim().isEmpty()) ? td.name.trim() : ("Napisy #" + td.id);
                IMedia.Track meta = subMeta.get(td.id);
                if (meta != null) {
                    StringBuilder extra = new StringBuilder();
                    String sc = mapCodecName(meta.codec != null && !meta.codec.isEmpty() ? meta.codec : meta.originalCodec);
                    if (!sc.isEmpty()) extra.append(sc);
                    if (meta.language != null && !meta.language.trim().isEmpty()
                            && !base.toLowerCase(Locale.ROOT).contains(meta.language.trim().toLowerCase(Locale.ROOT))) {
                        if (extra.length() > 0) extra.append(" ");
                        extra.append(meta.language.trim().toUpperCase(Locale.ROOT));
                    }
                    if (extra.length() > 0) {
                        base = base + "  (" + extra.toString() + ")";
                    }
                }
                foundSubs.add(new TrackItem(td.id, base, td.id == curSub, true));
            }
        }
        if (foundSubs.isEmpty()) {
            subItems.add(new TrackItem(-1, "Brak ścieżek napisów w strumieniu", false, false));
        } else {
            subItems.add(new TrackItem(-1, "Wyłącz napisy", curSub == -1, true));
            subItems.addAll(foundSubs);
        }
    }

    private static void renderTrackMenuUi(Activity a) {
        if (trackPanel == null || trackListBox == null) return;

        int audioRealCount = 0;
        for (TrackItem it : audioItems) {
            if (it.selectable && it.id != -1) audioRealCount++;
        }
        int subRealCount = 0;
        for (TrackItem it : subItems) {
            if (it.selectable && it.id != -1) subRealCount++;
        }

        tabAudioBtn.setText("AUDIO (" + audioRealCount + ")");
        tabSubBtn.setText("NAPISY (" + subRealCount + ")");

        if (trackTab == 0) {
            tabAudioBtn.setBackground(makeRoundedBg(Color.rgb(24, 92, 148), Color.rgb(102, 206, 250), Math.max(2, dp(a, 1.3f)), dp(a, 8)));
            tabAudioBtn.setTextColor(Color.WHITE);
            tabSubBtn.setBackground(makeRoundedBg(Color.argb(180, 20, 26, 38), Color.argb(110, 80, 96, 118), 1, dp(a, 8)));
            tabSubBtn.setTextColor(Color.rgb(175, 190, 208));
        } else {
            tabSubBtn.setBackground(makeRoundedBg(Color.rgb(24, 112, 86), Color.rgb(76, 224, 138), Math.max(2, dp(a, 1.3f)), dp(a, 8)));
            tabSubBtn.setTextColor(Color.WHITE);
            tabAudioBtn.setBackground(makeRoundedBg(Color.argb(180, 20, 26, 38), Color.argb(110, 80, 96, 118), 1, dp(a, 8)));
            tabAudioBtn.setTextColor(Color.rgb(175, 190, 208));
        }

        List<TrackItem> list = (trackTab == 0) ? audioItems : subItems;
        if (trackSelIdx < 0 || trackSelIdx >= list.size()) {
            trackSelIdx = 0;
        }

        trackListBox.removeAllViews();
        trackRowViews.clear();
        for (int i = 0; i < list.size(); i++) {
            TrackItem item = list.get(i);
            TextView row = makeTv(a, 15f, Color.WHITE, item.active);
            String prefix = item.active ? "●  " : "○  ";
            if (!item.selectable) prefix = "    ";
            row.setText(prefix + item.label);
            row.setPadding(dp(a, 14), dp(a, 10), dp(a, 14), dp(a, 10));
            LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            rlp.setMargins(0, dp(a, 3), 0, dp(a, 3));
            trackListBox.addView(row, rlp);
            trackRowViews.add(row);
        }
        highlightTrackRowsUi(a);
    }

    private static void highlightTrackRowsUi(Activity a) {
        List<TrackItem> list = (trackTab == 0) ? audioItems : subItems;
        int accColor = (trackTab == 0) ? Color.rgb(102, 206, 250) : Color.rgb(76, 224, 138);
        int focBg = (trackTab == 0) ? Color.rgb(28, 86, 138) : Color.rgb(24, 104, 78);

        for (int i = 0; i < trackRowViews.size() && i < list.size(); i++) {
            TextView row = trackRowViews.get(i);
            TrackItem item = list.get(i);
            if (i == trackSelIdx) {
                row.setBackground(makeRoundedBg(focBg, accColor, Math.max(2, dp(a, 1.5f)), dp(a, 8)));
                row.setTextColor(Color.WHITE);
            } else if (item.active) {
                row.setBackground(makeRoundedBg(Color.argb(180, 18, 38, 52), Color.argb(150, 86, 185, 225), 1, dp(a, 8)));
                row.setTextColor(accColor);
            } else {
                row.setBackground(makeRoundedBg(Color.argb(150, 18, 22, 32), Color.argb(70, 70, 82, 100), 1, dp(a, 8)));
                row.setTextColor(item.selectable ? Color.rgb(210, 220, 232) : Color.rgb(130, 142, 158));
            }
        }
        if (trackScroll != null && trackSelIdx >= 0 && trackSelIdx < trackRowViews.size()) {
            final View target = trackRowViews.get(trackSelIdx);
            trackScroll.post(new Runnable() {
                @Override
                public void run() {
                    int y = Math.max(0, target.getTop() - 40);
                    trackScroll.smoothScrollTo(0, y);
                }
            });
        }
    }

    private static void showTopPillUi(String msg, int durationMs) {
        if (topPill == null || msg == null || msg.isEmpty()) return;
        topPill.setText(msg);
        topPill.setVisibility(View.VISIBLE);
        topPill.bringToFront();
        uiHandler.removeCallbacks(hideTopPillRunnable);
        if (durationMs > 0) {
            uiHandler.postDelayed(hideTopPillRunnable, (long) durationMs);
        }
    }

    public static void openTrackMenu(final int startTab) {
        final Activity activity = getActivity();
        if (activity == null || mediaPlayer == null || !active) {
            return;
        }
        trackMenuOpen = true;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    ensureInitUi(activity);
                    if (mediaPlayer == null || !active) {
                        trackMenuOpen = false;
                        return;
                    }
                    if (infobar != null) {
                        infobar.setVisibility(View.GONE);
                        uiHandler.removeCallbacks(hideInfobarRunnable);
                        uiHandler.removeCallbacks(infobarTickRunnable);
                    }
                    populateTrackListsUi();
                    trackTab = (startTab == 1) ? 1 : 0;
                    List<TrackItem> list = (trackTab == 0) ? audioItems : subItems;
                    trackSelIdx = 0;
                    for (int i = 0; i < list.size(); i++) {
                        if (list.get(i).active) {
                            trackSelIdx = i;
                            break;
                        }
                    }
                    renderTrackMenuUi(activity);
                    trackPanel.setVisibility(View.VISIBLE);
                    trackPanel.bringToFront();
                } catch (Throwable t) {
                    trackMenuOpen = false;
                    Log.e(TAG, "openTrackMenu failed", t);
                }
            }
        });
    }

    public static void closeTrackMenu() {
        trackMenuOpen = false;
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (trackPanel != null) {
                    trackPanel.setVisibility(View.GONE);
                }
            }
        });
    }

    public static void moveTrackMenu(final int delta) {
        final Activity activity = getActivity();
        if (activity == null || !trackMenuOpen) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                List<TrackItem> list = (trackTab == 0) ? audioItems : subItems;
                if (list.isEmpty()) return;
                trackSelIdx = (trackSelIdx + delta) % list.size();
                if (trackSelIdx < 0) trackSelIdx += list.size();
                highlightTrackRowsUi(activity);
            }
        });
    }

    public static void toggleTrackTab() {
        final Activity activity = getActivity();
        if (activity == null || !trackMenuOpen) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                trackTab = (trackTab == 0) ? 1 : 0;
                List<TrackItem> list = (trackTab == 0) ? audioItems : subItems;
                trackSelIdx = 0;
                for (int i = 0; i < list.size(); i++) {
                    if (list.get(i).active) {
                        trackSelIdx = i;
                        break;
                    }
                }
                renderTrackMenuUi(activity);
            }
        });
    }

    public static void confirmTrackMenu() {
        final Activity activity = getActivity();
        if (activity == null || !trackMenuOpen) return;
        trackMenuOpen = false;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    List<TrackItem> list = (trackTab == 0) ? audioItems : subItems;
                    if (mediaPlayer != null && trackSelIdx >= 0 && trackSelIdx < list.size()) {
                        TrackItem item = list.get(trackSelIdx);
                        if (item.selectable) {
                            if (trackTab == 0) {
                                mediaPlayer.setAudioTrack(item.id);
                                showTopPillUi("AUDIO: " + item.label, 2800);
                            } else {
                                mediaPlayer.setSpuTrack(item.id);
                                showTopPillUi("NAPISY: " + item.label, 2800);
                            }
                        }
                    }
                } catch (Throwable t) {
                    Log.e(TAG, "confirmTrackMenu failed", t);
                } finally {
                    if (trackPanel != null) {
                        trackPanel.setVisibility(View.GONE);
                    }
                    refreshDynamicInfobarUi();
                }
            }
        });
    }

    private static String buildStreamDiagnostics() {
        if (mediaPlayer == null) {
            return ibSourceTag + "  •  Oczekiwanie na strumień...";
        }
        StringBuilder sb = new StringBuilder();
        IMedia m = null;
        try {
            m = mediaPlayer.getMedia();
            IMedia.Track[] freshTracks = getFreshTracks(m);

            IMedia.VideoTrack vt = null;
            IMedia.AudioTrack activeAt = null;
            IMedia.AudioTrack firstAt = null;
            int audioCount = 0;
            int subCount = 0;
            int selAudioId = -1;
            int selSubId = -1;
            int selVideoId = -1;
            try {
                selAudioId = mediaPlayer.getAudioTrack();
                selSubId = mediaPlayer.getSpuTrack();
                selVideoId = mediaPlayer.getVideoTrack();
            } catch (Throwable ignored) {
            }

            if (freshTracks != null) {
                for (IMedia.Track tr : freshTracks) {
                    if (tr == null) continue;
                    if (tr instanceof IMedia.VideoTrack) {
                        IMedia.VideoTrack cand = (IMedia.VideoTrack) tr;
                        if (vt == null) {
                            vt = cand;
                        } else if (cand.id == selVideoId && cand.width > 0) {
                            vt = cand;
                        } else if (vt.width <= 0 && cand.width > 0) {
                            vt = cand;
                        }
                    } else if (tr instanceof IMedia.AudioTrack) {
                        audioCount++;
                        IMedia.AudioTrack at = (IMedia.AudioTrack) tr;
                        if (firstAt == null) firstAt = at;
                        if (at.id == selAudioId) activeAt = at;
                    } else if (tr.type == IMedia.Track.Type.Text) {
                        subCount++;
                    }
                }
            }

            if (vt == null || vt.width <= 0) {
                try {
                    IMedia.VideoTrack curVt = mediaPlayer.getCurrentVideoTrack();
                    if (curVt != null && (vt == null || curVt.width > 0)) {
                        vt = curVt;
                    }
                } catch (Throwable ignored) {
                }
            }

            if (activeAt == null && selAudioId != -1) {
                activeAt = firstAt;
            }

            int vw = lastVideoW;
            int vh = lastVideoH;
            int sarN = lastSarNum;
            int sarD = lastSarDen;
            if (vt != null) {
                if (vt.width > 0 && vt.height > 0) {
                    if (vw <= 0 || vh <= 0) {
                        vw = vt.width;
                        vh = vt.height;
                    }
                }
                if (vt.sarNum > 0 && vt.sarDen > 0 && (sarN <= 0 || sarD <= 0)) {
                    sarN = vt.sarNum;
                    sarD = vt.sarDen;
                }
            }
            if (vw > 0 && vh > 0) {
                vh = normalizeHeight(vh);
                lastVideoW = vw;
                lastVideoH = vh;
            }

            if (vw > 0 && vh > 0) {
                String q = qualityTag(vw, vh);
                sb.append(vw).append("x").append(vh);
                if (!q.isEmpty()) sb.append(" ").append(q);
                String ar = aspectTag(vw, vh, sarN, sarD);
                if (!ar.isEmpty()) sb.append(" ").append(ar);
            }

            if (vt != null) {
                if (vt.frameRateNum > 0 && vt.frameRateDen > 0) {
                    double fps = (double) vt.frameRateNum / (double) vt.frameRateDen;
                    if (fps > 1.0 && fps < 240.0) {
                        if (sb.length() > 0) sb.append("  •  ");
                        if (Math.abs(fps - Math.rint(fps)) < 0.05) {
                            sb.append(Math.round(fps)).append(" fps");
                        } else {
                            sb.append(String.format(Locale.US, "%.2f fps", fps));
                        }
                    }
                }
                String vc = mapCodecName(vt.codec != null && !vt.codec.isEmpty() ? vt.codec : vt.originalCodec);
                if (!vc.isEmpty()) {
                    if (sb.length() > 0) sb.append("  •  ");
                    sb.append(vc);
                }
                if (vt.bitrate > 0) {
                    sb.append(String.format(Locale.US, " (%.1f Mbps)", vt.bitrate / 1000000.0));
                }
            }

            if (activeAt != null) {
                StringBuilder ab = new StringBuilder();
                String ac = mapCodecName(activeAt.codec != null && !activeAt.codec.isEmpty() ? activeAt.codec : activeAt.originalCodec);
                if (!ac.isEmpty()) ab.append(ac);
                String ch = channelsTag(activeAt.channels);
                if (!ch.isEmpty()) {
                    if (ab.length() > 0) ab.append(" ");
                    ab.append(ch);
                }
                if (activeAt.rate > 0) {
                    if (ab.length() > 0) ab.append(" ");
                    ab.append(String.format(Locale.US, "%.0f kHz", activeAt.rate / 1000.0));
                }
                if (activeAt.bitrate > 0) {
                    ab.append(" ").append(activeAt.bitrate / 1000).append("k");
                }
                if (activeAt.language != null && !activeAt.language.trim().isEmpty()) {
                    ab.append(" [").append(activeAt.language.trim().toUpperCase(Locale.ROOT)).append("]");
                }
                if (audioCount > 1) {
                    ab.append(" (").append(audioCount).append(" aud)");
                }
                if (ab.length() > 0) {
                    if (sb.length() > 0) sb.append("   |   ");
                    sb.append("AUDIO: ").append(ab);
                }
            } else if (selAudioId == -1 && audioCount > 0) {
                if (sb.length() > 0) sb.append("   |   ");
                sb.append("AUDIO: WYŁ");
            }

            if (subCount > 0) {
                if (sb.length() > 0) sb.append("   |   ");
                sb.append("SUB: ").append(selSubId != -1 ? "WŁ/" : "").append(subCount);
            }

            if (m != null) {
                try {
                    Method gm = m.getClass().getMethod("getStats");
                    Object st = gm.invoke(m);
                    if (st != null) {
                        float br = readFloatField(st, "demuxBitrate");
                        if (br <= 0.0001f) {
                            br = readFloatField(st, "inputBitrate");
                        }
                        if (br > 0.0001f) {
                            float mbps = br * 8.0f;
                            if (sb.length() > 0) sb.append("   |   ");
                            if (mbps >= 1.0f) {
                                sb.append(String.format(Locale.US, "%.1f Mbps", mbps));
                            } else {
                                sb.append(String.format(Locale.US, "%.0f kbps", mbps * 1000.0f));
                            }
                        }
                        int lost = readIntField(st, "lostPictures");
                        if (lost >= 0) {
                            if (sb.length() > 0) sb.append("  •  ");
                            sb.append("Drop: ").append(lost);
                        }
                    }
                } catch (Throwable ignored) {
                }
            }

            if (sb.length() > 0) sb.append("   |   ");
            sb.append("HW  •  ").append(ibSourceTag);
        } catch (Throwable t) {
            Log.w(TAG, "buildStreamDiagnostics error", t);
        } finally {
            if (m != null) {
                try {
                    m.release();
                } catch (Throwable ignored) {
                }
            }
        }
        return sb.length() > 0 ? sb.toString() : (ibSourceTag + "  •  Analiza strumienia...");
    }

    private static void refreshDynamicInfobarUi() {
        if (infobar == null) return;

        Calendar cal = Calendar.getInstance();
        String dName = PL_DAYS_SHORT[cal.get(Calendar.DAY_OF_WEEK)];
        String mName = PL_MONTHS_SHORT[cal.get(Calendar.MONTH)];
        String clockStr = String.format(Locale.ROOT, "%02d:%02d:%02d  •  %s, %d %s",
                cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE), cal.get(Calendar.SECOND),
                dName, cal.get(Calendar.DAY_OF_MONTH), mName);
        if (ibClock != null) {
            ibClock.setText(clockStr);
        }

        if (ibLiveBadge != null) {
            String st = lastStatus != null ? lastStatus : "";
            if (st.contains("BŁĄD")) {
                ibLiveBadge.setText("BŁĄD");
                ibLiveBadge.setBackground(makeRoundedBg(Color.rgb(225, 72, 72), 0, 0, 8));
            } else if (st.contains("Bufor") || st.contains("Łączenie")) {
                ibLiveBadge.setText("BUFOR");
                ibLiveBadge.setBackground(makeRoundedBg(Color.rgb(240, 195, 65), 0, 0, 8));
            } else {
                ibLiveBadge.setText("LIVE");
                ibLiveBadge.setBackground(makeRoundedBg(Color.rgb(76, 224, 138), 0, 0, 8));
            }
        }

        long nowSec = System.currentTimeMillis() / 1000L;
        if (ibNowBeginSec > 0L && ibNowDurSec > 0L) {
            long elapsed = Math.max(0L, nowSec - ibNowBeginSec);
            long remSec = Math.max(0L, (ibNowBeginSec + ibNowDurSec) - nowSec);
            int remMin = (int) ((remSec + 59L) / 60L);
            float frac = Math.max(0f, Math.min(1f, (float) elapsed / (float) ibNowDurSec));
            float wFill = frac * 1000f;
            if (ibProgFill != null && ibProgEmpty != null) {
                ibProgFill.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, wFill));
                ibProgEmpty.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1000f - wFill));
            }
            if (ibNowRem != null) {
                ibNowRem.setText("+" + remMin + " min");
            }
        } else {
            if (ibProgFill != null && ibProgEmpty != null) {
                ibProgFill.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0f));
                ibProgEmpty.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1000f));
            }
            if (ibNowRem != null) {
                ibNowRem.setText("");
            }
        }

        if (ibStreamInfo != null) {
            ibStreamInfo.setText(buildStreamDiagnostics());
        }
    }

    private static void applyPiconUi(String piconPath) {
        if (ibPicon == null || ibPiconWrap == null) return;
        if (piconPath == null || piconPath.isEmpty()) {
            ibLoadedPiconPath = "";
            ibPicon.setImageBitmap(null);
            ibPiconWrap.setVisibility(View.GONE);
            return;
        }
        if (piconPath.equals(ibLoadedPiconPath) && ibPiconWrap.getVisibility() == View.VISIBLE) {
            return;
        }
        File f = new File(piconPath);
        if (!f.exists() || !f.isFile()) {
            ibLoadedPiconPath = "";
            ibPicon.setImageBitmap(null);
            ibPiconWrap.setVisibility(View.GONE);
            return;
        }
        try {
            Bitmap bmp = BitmapFactory.decodeFile(piconPath);
            if (bmp != null) {
                ibLoadedPiconPath = piconPath;
                ibPicon.setImageBitmap(bmp);
                ibPiconWrap.setVisibility(View.VISIBLE);
            } else {
                ibPiconWrap.setVisibility(View.GONE);
            }
        } catch (Throwable ignored) {
            ibPiconWrap.setVisibility(View.GONE);
        }
    }

    private static int[] applyBoundsUi(final Activity activity, double fx, double fy, double fw, double fh, boolean fullscreen) {
        inFullscreen = fullscreen;
        FrameLayout content = (FrameLayout) activity.findViewById(android.R.id.content);
        int cw = content != null ? content.getWidth() : 0;
        int ch = content != null ? content.getHeight() : 0;
        if (cw <= 0 || ch <= 0) {
            DisplayMetrics dm = activity.getResources().getDisplayMetrics();
            cw = dm.widthPixels;
            ch = dm.heightPixels;
        }

        int left, top, w, h;
        FrameLayout.LayoutParams lp;
        if (fullscreen) {
            left = 0;
            top = 0;
            w = cw;
            h = ch;
            lp = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT
            );
            lp.gravity = Gravity.TOP | Gravity.LEFT;
        } else {
            left = Math.max(0, (int) Math.round(fx * cw));
            top = Math.max(0, (int) Math.round(fy * ch));
            w = Math.max(2, (int) Math.round(fw * cw));
            h = Math.max(2, (int) Math.round(fh * ch));
            lp = new FrameLayout.LayoutParams(w, h);
            lp.gravity = Gravity.TOP | Gravity.LEFT;
            lp.leftMargin = left;
            lp.topMargin = top;
        }

        videoView.setLayoutParams(lp);
        videoView.setVisibility(View.VISIBLE);
        videoView.bringToFront();
        if (!fullscreen) {
            trackMenuOpen = false;
            if (trackPanel != null) trackPanel.setVisibility(View.GONE);
            if (topPill != null) topPill.setVisibility(View.GONE);
            if (infobar != null) {
                infobar.setVisibility(View.GONE);
                uiHandler.removeCallbacks(hideInfobarRunnable);
                uiHandler.removeCallbacks(infobarTickRunnable);
            }
        } else {
            if (infobar != null) infobar.bringToFront();
            if (trackPanel != null && trackMenuOpen) trackPanel.bringToFront();
            if (topPill != null) topPill.bringToFront();
        }
        return new int[]{w, h};
    }

    public static void play(final String url, final double fx, final double fy, final double fw, final double fh, final boolean fullscreen) {
        final Activity activity = getActivity();
        if (activity == null || url == null || url.isEmpty()) {
            lastStatus = "VLC BŁĄD: brak aktywności lub URL";
            return;
        }
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    ensureInitUi(activity);
                    boolean samePlaying = url.equals(currentUrl) && active && mediaPlayer != null && mediaPlayer.isPlaying();
                    if (!samePlaying) {
                        trackMenuOpen = false;
                        if (trackPanel != null) trackPanel.setVisibility(View.GONE);
                        lastVideoW = 0;
                        lastVideoH = 0;
                        lastSarNum = 0;
                        lastSarDen = 0;
                        recreateMediaPlayerUi();
                    }

                    int[] wh = applyBoundsUi(activity, fx, fy, fw, fh, fullscreen);

                    IVLCVout vout = mediaPlayer.getVLCVout();
                    if (!vout.areViewsAttached()) {
                        vout.setVideoView(videoView);
                        vout.attachViews(videoLayoutListener);
                    }
                    vout.setWindowSize(wh[0], wh[1]);

                    if (!samePlaying) {
                        currentUrl = url;
                        lastStatus = "VLC: Łączenie...";
                        Media media = new Media(libVlc, Uri.parse(url));
                        media.setHWDecoderEnabled(true, false);
                        media.addOption(":network-caching=800");
                        media.addOption(":live-caching=800");
                        mediaPlayer.setMedia(media);
                        media.release();
                        mediaPlayer.setScale(0f);
                        mediaPlayer.setAspectRatio(null);
                        mediaPlayer.play();
                        mediaPlayer.setVolume(volume);
                        active = true;
                    }
                } catch (Throwable t) {
                    lastStatus = "VLC WYJĄTEK: " + t.getClass().getSimpleName() + ": " + t.getMessage();
                    Log.e(TAG, "play failed", t);
                }
            }
        });
    }

    public static void setBounds(final double fx, final double fy, final double fw, final double fh, final boolean fullscreen) {
        final Activity activity = getActivity();
        if (activity == null) {
            return;
        }
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    if (videoView == null || mediaPlayer == null || !active) {
                        return;
                    }
                    int[] wh = applyBoundsUi(activity, fx, fy, fw, fh, fullscreen);
                    IVLCVout vout = mediaPlayer.getVLCVout();
                    if (!vout.areViewsAttached()) {
                        vout.setVideoView(videoView);
                        vout.attachViews(videoLayoutListener);
                    }
                    vout.setWindowSize(wh[0], wh[1]);
                } catch (Throwable t) {
                    Log.e(TAG, "setBounds failed", t);
                }
            }
        });
    }

    public static void showInfobarRich(
            final String chTitle,
            final String nowTime,
            final String nowTitle,
            final long nowBeginSec,
            final long nowDurSec,
            final String nextTime,
            final String nextTitle,
            final String nextDurText,
            final String piconPath,
            final String sourceTag,
            final int durationMs
    ) {
        final Activity activity = getActivity();
        if (activity == null) {
            return;
        }
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    ensureInitUi(activity);
                    if (infobar == null) return;
                    ibNowBeginSec = nowBeginSec;
                    ibNowDurSec = nowDurSec;
                    ibSourceTag = (sourceTag == null || sourceTag.isEmpty()) ? "ENIGMA2" : sourceTag;

                    ibChannel.setText(chTitle == null ? "" : chTitle);
                    ibNowTime.setText(nowTime == null ? "" : nowTime);
                    ibNowTitle.setText((nowTitle == null || nowTitle.isEmpty()) ? "Brak danych EPG" : nowTitle);
                    ibNextTime.setText(nextTime == null ? "" : nextTime);
                    ibNextTitle.setText((nextTitle == null || nextTitle.isEmpty()) ? "Brak kolejnej audycji" : nextTitle);
                    ibNextDur.setText(nextDurText == null ? "" : nextDurText);

                    applyPiconUi(piconPath);
                    refreshDynamicInfobarUi();

                    infobar.setVisibility(View.VISIBLE);
                    infobar.bringToFront();

                    uiHandler.removeCallbacks(hideInfobarRunnable);
                    uiHandler.removeCallbacks(infobarTickRunnable);
                    uiHandler.postDelayed(infobarTickRunnable, 500L);
                    if (durationMs > 0) {
                        uiHandler.postDelayed(hideInfobarRunnable, (long) durationMs);
                    }
                } catch (Throwable t) {
                    Log.e(TAG, "showInfobarRich failed", t);
                }
            }
        });
    }

    public static void updateInfobarPicon(final String piconPath) {
        final Activity activity = getActivity();
        if (activity == null || piconPath == null || piconPath.isEmpty()) {
            return;
        }
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                applyPiconUi(piconPath);
            }
        });
    }

    public static void showToast(final String title, final String subtitle, final int durationMs) {
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                String msg = (title != null && !title.isEmpty()) ? (title + ": " + (subtitle == null ? "" : subtitle)) : (subtitle == null ? "" : subtitle);
                showTopPillUi(msg, durationMs > 0 ? durationMs : 2200);
            }
        });
    }

    public static void hideInfobar() {
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                uiHandler.removeCallbacks(hideInfobarRunnable);
                uiHandler.removeCallbacks(infobarTickRunnable);
                if (infobar != null) {
                    infobar.setVisibility(View.GONE);
                }
            }
        });
    }

    public static void toggleInfobar() {
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (infobar == null) return;
                if (infobar.getVisibility() == View.VISIBLE) {
                    uiHandler.removeCallbacks(hideInfobarRunnable);
                    uiHandler.removeCallbacks(infobarTickRunnable);
                    infobar.setVisibility(View.GONE);
                } else {
                    refreshDynamicInfobarUi();
                    infobar.setVisibility(View.VISIBLE);
                    infobar.bringToFront();
                    uiHandler.removeCallbacks(hideInfobarRunnable);
                    uiHandler.removeCallbacks(infobarTickRunnable);
                    uiHandler.postDelayed(infobarTickRunnable, 500L);
                    uiHandler.postDelayed(hideInfobarRunnable, 6000L);
                }
            }
        });
    }

    public static void setVolume(final int vol) {
        volume = Math.max(0, Math.min(100, vol));
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (mediaPlayer != null) {
                    try {
                        mediaPlayer.setVolume(volume);
                    } catch (Throwable ignored) {
                    }
                }
            }
        });
    }

    public static void hide() {
        trackMenuOpen = false;
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                uiHandler.removeCallbacks(hideInfobarRunnable);
                uiHandler.removeCallbacks(infobarTickRunnable);
                uiHandler.removeCallbacks(hideTopPillRunnable);
                try {
                    if (mediaPlayer != null) {
                        mediaPlayer.stop();
                        IVLCVout vout = mediaPlayer.getVLCVout();
                        if (vout.areViewsAttached()) {
                            vout.detachViews();
                        }
                    }
                    if (videoView != null) videoView.setVisibility(View.GONE);
                    if (infobar != null) infobar.setVisibility(View.GONE);
                    if (trackPanel != null) trackPanel.setVisibility(View.GONE);
                    if (topPill != null) topPill.setVisibility(View.GONE);
                } catch (Throwable t) {
                    Log.e(TAG, "hide failed", t);
                }
                active = false;
                inFullscreen = false;
                currentUrl = "";
                lastVideoW = 0;
                lastVideoH = 0;
            }
        });
    }

    public static void stopAndRelease() {
        trackMenuOpen = false;
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                uiHandler.removeCallbacks(hideInfobarRunnable);
                uiHandler.removeCallbacks(infobarTickRunnable);
                uiHandler.removeCallbacks(hideTopPillRunnable);
                try {
                    if (mediaPlayer != null) {
                        mediaPlayer.stop();
                        IVLCVout vout = mediaPlayer.getVLCVout();
                        if (vout.areViewsAttached()) {
                            vout.detachViews();
                        }
                        mediaPlayer.release();
                        mediaPlayer = null;
                    }
                    if (libVlc != null) {
                        libVlc.release();
                        libVlc = null;
                    }
                } catch (Throwable t) {
                    Log.e(TAG, "stopAndRelease failed", t);
                }
                active = false;
                inFullscreen = false;
                currentUrl = "";
                lastVideoW = 0;
                lastVideoH = 0;
            }
        });
    }

    public static void finishApp() {
        trackMenuOpen = false;
        final Activity activity = getActivity();
        if (activity == null) return;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                uiHandler.removeCallbacks(hideInfobarRunnable);
                uiHandler.removeCallbacks(infobarTickRunnable);
                uiHandler.removeCallbacks(hideTopPillRunnable);
                try {
                    if (mediaPlayer != null) {
                        mediaPlayer.stop();
                        IVLCVout vout = mediaPlayer.getVLCVout();
                        if (vout.areViewsAttached()) {
                            vout.detachViews();
                        }
                        mediaPlayer.release();
                        mediaPlayer = null;
                    }
                    if (libVlc != null) {
                        libVlc.release();
                        libVlc = null;
                    }
                } catch (Throwable ignored) {
                }
                active = false;
                inFullscreen = false;
                currentUrl = "";
                try {
                    activity.finishAndRemoveTask();
                } catch (Throwable t) {
                    try {
                        activity.finish();
                    } catch (Throwable ignored) {
                    }
                }
                uiHandler.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        android.os.Process.killProcess(android.os.Process.myPid());
                    }
                }, 120L);
            }
        });
    }
}
"""


def _patch_manifest(manifest_file):
    text = manifest_file.read_text(encoding="utf-8")

    if 'android:hardwareAccelerated="false"' in text:
        text = text.replace('android:hardwareAccelerated="false"', 'android:hardwareAccelerated="true"')

    extras = []
    if "usesCleartextTraffic" not in text:
        extras.append('android:usesCleartextTraffic="true"')
    if "hardwareAccelerated" not in text:
        extras.append('android:hardwareAccelerated="true"')
    if "android:banner=" not in text:
        extras.append('android:banner="@drawable/banner"')
    if extras:
        text = text.replace("<application ", f"<application {' '.join(extras)} ", 1)

    if "android.software.leanback" not in text:
        features = (
            '    <uses-feature android:name="android.software.leanback" android:required="false" />\n'
            '    <uses-feature android:name="android.hardware.touchscreen" android:required="false" />\n'
        )
        text = text.replace("<application ", f"{features}    <application ", 1)

    if "<activity " in text and 'android:banner="@drawable/banner"' not in text.split("<activity ", 1)[1]:
        text = text.replace("<activity ", '<activity android:banner="@drawable/banner" ', 1)

    if "LEANBACK_LAUNCHER" not in text:
        text = re.sub(
            r'(<category\s+android:name="android\.intent\.category\.LAUNCHER"\s*/>)',
            r'\1\n                <category android:name="android.intent.category.LEANBACK_LAUNCHER" />',
            text,
            count=1,
        )

    manifest_file.write_text(text, encoding="utf-8")
    print(f"[HOOK] Zaktualizowano manifest dla Android TV: {manifest_file}")


def _copy_banner(dist_dir):
    banner_src = Path(__file__).resolve().parent / "banner.png"
    if banner_src.exists():
        for bucket in ("drawable", "drawable-xhdpi", "drawable-nodpi"):
            drawable_dir = dist_dir / "src" / "main" / "res" / bucket
            drawable_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(banner_src, drawable_dir / "banner.png")
        print("[HOOK] Skopiowano baner do drawable / drawable-xhdpi / drawable-nodpi")


def _patch_python_activity(dist_dir):
    pa = dist_dir / "src" / "main" / "java" / "org" / "kivy" / "android" / "PythonActivity.java"
    if not pa.exists():
        print(f"[HOOK] UWAGA: nie znaleziono {pa}")
        return
    text = pa.read_text(encoding="utf-8")
    if "pollTvKey()" in text:
        if "KEYCODE_MEDIA_AUDIO_TRACK" not in text:
            text = text.replace(
                "case android.view.KeyEvent.KEYCODE_INFO:",
                "case android.view.KeyEvent.KEYCODE_INFO:\n"
                "            case android.view.KeyEvent.KEYCODE_CAPTIONS:\n"
                "            case android.view.KeyEvent.KEYCODE_MEDIA_AUDIO_TRACK:",
                1,
            )
            pa.write_text(text, encoding="utf-8")
            print(f"[HOOK] Dopisano klawisze AUDIO/CAPTIONS do: {pa}")
        else:
            print("[HOOK] PythonActivity.java już ma pełną obsługę pilota Android TV")
        return

    if re.search(r"public\s+boolean\s+dispatchKeyEvent\s*\([^)]*\)\s*\{", text):
        text = re.sub(
            r"(public\s+class\s+PythonActivity\s+extends\s+\w+\s*\{)",
            r"\1" + TV_KEY_FIELDS,
            text,
            count=1,
        )
        text = re.sub(
            r"(public\s+boolean\s+dispatchKeyEvent\s*\([^)]*\)\s*\{)",
            r"\1" + TV_DISPATCH_BODY,
            text,
            count=1,
        )
    else:
        dispatch_method = (
            "\n    @Override\n"
            "    public boolean dispatchKeyEvent(android.view.KeyEvent event) {"
            + TV_DISPATCH_BODY
            + "        return super.dispatchKeyEvent(event);\n    }\n"
        )
        text = re.sub(
            r"(public\s+class\s+PythonActivity\s+extends\s+\w+\s*\{)",
            r"\1" + TV_KEY_FIELDS + dispatch_method,
            text,
            count=1,
        )

    pa.write_text(text, encoding="utf-8")
    print(f"[HOOK] Dodano przechwytywanie pilota Android TV do: {pa}")


def _write_tv_vlc_controller(dist_dir):
    java_dir = dist_dir / "src" / "main" / "java" / "org" / "kivy" / "android"
    java_dir.mkdir(parents=True, exist_ok=True)
    target = java_dir / "TvVlcController.java"
    target.write_text(TV_VLC_CONTROLLER_JAVA, encoding="utf-8")
    print(f"[HOOK] Zapisano natywny odtwarzacz LibVLC v1.2 (fresh nativeGetTracks): {target}")


def _slim_gradle(dist_dir):
    gradle_file = dist_dir / "build.gradle"
    if not gradle_file.exists():
        return
    g = gradle_file.read_text(encoding="utf-8")

    if "abiFilters" not in g:
        g = re.sub(
            r"(?m)^([ \t]*)defaultConfig\s*\{",
            r"\1defaultConfig {\n\1    ndk {\n\1        abiFilters 'armeabi-v7a'\n\1    }",
            g,
            count=1,
        )

    if "useLegacyPackaging" not in g:
        g = re.sub(
            r"(?m)^android\s*\{",
            "android {\n    packaging {\n        jniLibs {\n            useLegacyPackaging = true\n        }\n    }",
            g,
            count=1,
        )

    gradle_file.write_text(g, encoding="utf-8")
    print(f"[HOOK] Odchudzono build.gradle (tylko armeabi-v7a + kompresja .so): {gradle_file}")


def before_apk_build(toolchain):
    dist_dir = Path(toolchain._dist.dist_dir)
    for rel in ("templates/AndroidManifest.tmpl.xml", "src/main/AndroidManifest.xml"):
        mf = dist_dir / rel
        if mf.exists():
            _patch_manifest(mf)
    _copy_banner(dist_dir)


def before_apk_assemble(toolchain):
    dist_dir = Path(toolchain._dist.dist_dir)
    _copy_banner(dist_dir)
    _patch_python_activity(dist_dir)
    _write_tv_vlc_controller(dist_dir)
    _slim_gradle(dist_dir)

    assets_dir = dist_dir / "src" / "main" / "assets"
    if assets_dir.exists():
        for item in assets_dir.glob("*arm64*"):
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            print(f"[HOOK] Usunięto zbędny pakiet 64-bit z assets: {item.name}")
