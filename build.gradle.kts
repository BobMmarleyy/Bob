import com.lagradost.cloudstream3.gradle.CloudstreamExtension
import com.android.build.gradle.BaseExtension

buildscript {
    repositories {
        google()
        mavenCentral()
        // JitPack'in recloudstream artifact'ları için maven-metadata bozuk
        // (gradle--32895aedb6-1.pom 404 veriyor). Bu ivy repo, maven-metadata'i
        // tamamen atlayıp gradle--SNAPSHOT.jar dosyasını doğrudan çekiyor.
        // NOT: gerçek plugin sınıfları SUBPROJECT koordinatında
        // com.github.recloudstream.gradle:gradle (kök jar sadece manifest).
        // ivy transitive POM vermediği için stdlib/asm/jadb açıkça declare ediliyor.
        ivy {
            url = uri("https://jitpack.io")
            patternLayout {
                artifact("[organisation]/[module]/[revision]/[artifact]-[revision].[ext]")
                setM2compatible(true)
            }
            metadataSources {
                artifact()
            }
            content {
                includeGroupByRegex("com\\.github\\.recloudstream\\.gradle")
            }
        }
        maven("https://jitpack.io")
    }

    dependencies {
        classpath("com.android.tools.build:gradle:8.7.3")
        // Cloudstream gradle plugin (subproject koordinatı — gerçek sınıflar burada)
        classpath("com.github.recloudstream.gradle:gradle:-SNAPSHOT")
        classpath("org.ow2.asm:asm:9.9.1")
        classpath("org.ow2.asm:asm-tree:9.9.1")
        classpath("com.github.vidstige:jadb:v1.2.1")
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.0")
    }
}

allprojects {
    repositories {
        google()
        mavenCentral()
        maven("https://jitpack.io")
    }
}

fun Project.cloudstream(configuration: CloudstreamExtension.() -> Unit) = extensions.getByName<CloudstreamExtension>("cloudstream").configuration()

fun Project.android(configuration: BaseExtension.() -> Unit) = extensions.getByName<BaseExtension>("android").configuration()

subprojects {
    apply(plugin = "com.android.library")
    apply(plugin = "kotlin-android")
    apply(plugin = "com.lagradost.cloudstream3.gradle")

    cloudstream {
        // when running through github workflow, GITHUB_REPOSITORY should contain current repository name
        setRepo(System.getenv("GITHUB_REPOSITORY") ?: "https://github.com/BobMmarleyy/Bob")

        authors = listOf("kraptor")
    }

    android {
        namespace = "com.kraptor"

        defaultConfig {
            minSdk = 21
            compileSdkVersion(35)
            targetSdk = 35
        }

        compileOptions {
            sourceCompatibility = JavaVersion.VERSION_17
            targetCompatibility = JavaVersion.VERSION_17
        }

        tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinJvmCompile> {
            compilerOptions {
                jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
                freeCompilerArgs.addAll(
                    listOf(
                        "-Xno-call-assertions",
                        "-Xno-param-assertions",
                        "-Xno-receiver-assertions"
                    )
                )
            }
        }
    }


    dependencies {
        val cloudstream by configurations
        val implementation by configurations

        // Stubs for all Cloudstream classes
        cloudstream("com.lagradost:cloudstream3:pre-release")

        // these dependencies can include any of those which are added by the app,
        // but you dont need to include any of them if you dont need them
        // https://github.com/recloudstream/cloudstream/blob/master/app/build.gradle
        implementation(kotlin("stdlib"))                                              // Kotlin'in temel kütüphanesi
        implementation("com.github.Blatzar:NiceHttp:0.4.13")                          // HTTP kütüphanesi
        implementation("org.jsoup:jsoup:1.19.1")                                      // HTML ayrıştırıcı
        implementation("com.fasterxml.jackson.module:jackson-module-kotlin:2.13.1")   // Kotlin için Jackson JSON kütüphanesi
        implementation("com.fasterxml.jackson.core:jackson-databind:2.16.0")          // JSON-nesne dönüştürme kütüphanesi
        implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.1")      // Kotlin için asenkron işlemler
        implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.0")
        // cloudstream3 stub'ı @Nullable (jspecify) kullanıyor — eklenti classpath'inde olmalı
        implementation("org.jspecify:jspecify:1.0.0")
    }
}

task<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
