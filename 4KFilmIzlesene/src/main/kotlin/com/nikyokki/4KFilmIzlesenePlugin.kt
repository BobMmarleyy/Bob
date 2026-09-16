package com.nikyokki

import android.content.Context
import com.lagradost.cloudstream3.plugins.BasePlugin
import com.lagradost.cloudstream3.plugins.CloudstreamPlugin
import com.lagradost.cloudstream3.plugins.Plugin

@CloudstreamPlugin
class `4KFilmIzlesenePlugin`: BasePlugin() {
    override fun load() {
        registerMainAPI(`4KFilmIzlesene`())
    }
}