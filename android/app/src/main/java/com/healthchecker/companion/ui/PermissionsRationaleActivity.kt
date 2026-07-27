package com.healthchecker.companion.ui

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.healthchecker.companion.R

class PermissionsRationaleActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val tv = TextView(this)
        tv.text = getString(R.string.permissions_rationale)
        tv.setPadding(48, 48, 48, 48)
        setContentView(tv)
    }
}
